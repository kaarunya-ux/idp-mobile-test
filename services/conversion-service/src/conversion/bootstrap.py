"""Composition root: builds the long-lived objects once per process (DI-01, DI-03).

The only module allowed to import from every layer. Each entry point (``consumers``, ``worker``,
``outbox_relay``) builds what it needs here, so there is one wiring for all of them.
"""

import os
import socket
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import httpx
from botocore.config import Config
from celery import Celery
from faststream.rabbit import RabbitBroker

from conversion.conversion.application.services.accept_conversion import AcceptConversionService
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.application.services.convert_now import ConvertNowService
from conversion.conversion.application.services.edit_pdf import EditHandler
from conversion.conversion.application.services.export_results import ExportHandler
from conversion.conversion.application.services.process_conversion import OutputBuckets, ProcessConversionService
from conversion.conversion.application.services.run_job import (
    ConvertHandler,
    JobRunner,
    MergeHandler,
    PreviewHandler,
)
from conversion.conversion.domain.ports import ObjectStoragePort, PageReaderPort, UnitOfWorkFactory
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.domain.values import JobType, ThumbnailSpec
from conversion.conversion.infrastructure.converters.docx_to_model import PythonDocxReader
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter
from conversion.conversion.infrastructure.converters.images_to_pdf import Img2PdfImageWrapper
from conversion.conversion.infrastructure.converters.model_to_docx import PythonDocxWriter
from conversion.conversion.infrastructure.converters.model_to_pptx import PythonPptxWriter
from conversion.conversion.infrastructure.converters.model_to_sheets import CsvModelWriter, OpenpyxlModelWriter
from conversion.conversion.infrastructure.converters.ocr_to_model import ModelFirstOcrConverter
from conversion.conversion.infrastructure.converters.office_to_pdf import LibreOfficePdfConverter
from conversion.conversion.infrastructure.converters.pdf_merge import PikepdfMerger
from conversion.conversion.infrastructure.converters.pdf_to_images import PdfiumPageRenderer
from conversion.conversion.infrastructure.converters.pdf_to_model import PdfplumberReader
from conversion.conversion.infrastructure.converters.pptx_from_images import PptxImageSlides
from conversion.conversion.infrastructure.converters.pptx_to_model import PythonPptxReader
from conversion.conversion.infrastructure.converters.sheet_to_model import SheetReader
from conversion.conversion.infrastructure.converters.thumbnails import PillowThumbnailer
from conversion.conversion.infrastructure.converters.xlsx_csv import OpenpyxlCsvConverter
from conversion.conversion.infrastructure.export.exporter import ResultExporter
from conversion.conversion.infrastructure.inspection import PdfiumFileInspector
from conversion.conversion.infrastructure.messaging.messages import PRODUCER
from conversion.conversion.infrastructure.messaging.publisher import RabbitEventPublisher
from conversion.conversion.infrastructure.pdfedit.editor import PikepdfEditor
from conversion.conversion.infrastructure.persistence.repository import SqlUnitOfWork
from conversion.conversion.infrastructure.readers.model_reader import GatewayPageReader
from conversion.conversion.infrastructure.readers.ocr_layout_reader import OcrLayoutPageReader
from conversion.conversion.infrastructure.registry import ConverterRegistry
from conversion.conversion.infrastructure.secrets import RsaSecrets
from conversion.conversion.infrastructure.storage import S3ObjectStorage
from conversion.conversion.infrastructure.tasks import CeleryTaskQueue
from conversion.core.clock import Clock, SystemClock
from conversion.core.config import OcrSettings, Settings, StorageSettings
from conversion.core.db import SessionFactory, create_db_engine, create_session_factory
from conversion.core.outbox import OutboxRelay
from conversion.core.service_auth import ServiceTokenProvider
from idp_ocr_engines.engines.ppocr import PpOcrEngine, PpOcrModels
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.port import OcrEnginePort
from idp_ocr_engines.registry import ModelRegistry

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client


def build_converter_registry(settings: Settings, storage: ObjectStoragePort | None = None) -> ConverterRegistry:
    """Every converter this build of the service can run. The scanned routes' reader joins only when ai-gateway and
    storage are configured; without them those routes answer ``CNV-422-ROUTE-001``, as before."""
    limits, office = settings.limits, settings.office
    return ConverterRegistry(
        [
            *_scanned_reader(settings),
            PillowImageFormatConverter(max_pixels=limits.max_image_pixels),
            PdfiumPageRenderer(max_pages=limits.max_pages, max_side_px=limits.max_render_side_px),
            Img2PdfImageWrapper(max_pixels=limits.max_image_pixels),
            PptxImageSlides(max_pixels=limits.max_image_pixels),
            LibreOfficePdfConverter(binary=office.binary, timeout=office.timeout, keep_warm=office.keep_warm),
            OpenpyxlCsvConverter(),
            PythonDocxReader(),
            SheetReader(),
            PythonPptxReader(),
            PdfplumberReader(max_pages=limits.max_pages),
            PythonDocxWriter(max_table_rows=limits.max_document_table_rows),
            PythonPptxWriter(max_table_rows=limits.max_document_table_rows),
            OpenpyxlModelWriter(),
            CsvModelWriter(),
        ]
    )


def _scanned_reader(settings: Settings) -> list[ModelFirstOcrConverter]:
    """The scanned routes' reader: ai-gateway and this service's login to it, model-first or OCR-first
    (``AI_GATEWAY__PAGE_READER``, ADR 0001). No storage: each page picture goes inside the call."""
    gateway, auth = settings.ai_gateway, settings.service_auth
    if gateway.url is None or auth is None:
        return []
    tokens = ServiceTokenProvider(httpx.Client(timeout=auth.timeout.total_seconds()), auth)
    seconds = gateway.timeout.total_seconds()
    client = httpx.Client(
        base_url=str(gateway.url).rstrip("/"), timeout=httpx.Timeout(seconds, connect=min(2.0, seconds))
    )
    reader: PageReaderPort = GatewayPageReader(client, tokens)
    if gateway.page_reader == "ocr_layout":
        reader = OcrLayoutPageReader(partial(_ocr_engine, settings.ocr), client, tokens, fallback=reader)
    return [
        ModelFirstOcrConverter(
            reader,
            render_dpi=gateway.render_dpi,
            render_max_side_px=gateway.render_max_side_px,
            max_pages=gateway.max_pages,
            concurrency=gateway.page_concurrency,
            max_pixels=settings.limits.max_image_pixels,
        )
    ]


def _ocr_engine(ocr: OcrSettings) -> OcrEnginePort:
    """Extraction's engine A (PP-OCRv6 small), its checksums verified as it loads (ML-01)."""
    return PpOcrEngine.from_registry(
        engine_id="ocr-a",
        registry=ModelRegistry.from_file(Path(ocr.models_file)),
        models_dir=ocr.models_dir,
        models=PpOcrModels(detector="ppocrv6-small-det", recognizer="ppocrv6-small-rec"),
        limits=RuntimeLimits(threads=ocr.threads),
    )


def build_convert_file_service(settings: Settings, storage: ObjectStoragePort | None = None) -> ConvertFileService:
    return ConvertFileService(RoutePlanner(), build_converter_registry(settings, storage))


def build_convert_now_service(settings: Settings) -> ConvertNowService:
    """The synchronous route's use case (ADR 0002): the libraries, and the scanned reader for scans and photos when
    ai-gateway is configured (each picture goes inside the call); no storage and no database."""
    warm = settings.model_copy(update={"office": settings.office.model_copy(update={"keep_warm": True})})
    return ConvertNowService(
        RoutePlanner(),
        build_convert_file_service(warm),
        PdfiumFileInspector(),
        PikepdfMerger(max_pages=settings.limits.max_pages),
        reads=_scanned_reader_configured(settings),
        max_read_pages=settings.sync.max_read_pages,
    )


def _scanned_reader_configured(settings: Settings) -> bool:
    """As ``_scanned_reader`` decides: ai-gateway and this service's login to it."""
    return settings.ai_gateway.url is not None and settings.service_auth is not None


def build_job_runner(settings: Settings, storage: ObjectStoragePort) -> JobRunner:
    limits, preview, edit = settings.limits, settings.preview, settings.edit
    converter = build_convert_file_service(settings, storage)
    private_key = settings.crypto.private_key
    return JobRunner(
        {
            JobType.CONVERSION: ConvertHandler(converter),
            JobType.PDF_MERGE: MergeHandler(converter, PikepdfMerger(max_pages=limits.max_pages)),
            JobType.PREVIEW: PreviewHandler(
                converter,
                PillowThumbnailer(max_pixels=limits.max_image_pixels),
                spec=ThumbnailSpec(max_side_px=preview.max_side_px, jpeg_quality=preview.jpeg_quality),
            ),
            JobType.PDF_EDIT: EditHandler(
                PikepdfEditor(max_pages=limits.max_pages, redaction_dpi=edit.redaction_dpi),
                storage,
                RsaSecrets(private_key.get_secret_value() if private_key else None),
                max_image_bytes=int(edit.max_image_bytes),
            ),
            JobType.EXPORT: ExportHandler(
                ResultExporter(),
                converter,
                storage,
                derived_bucket=settings.storage.bucket_derived if settings.storage else "idp-derived",
            ),
        }
    )


STALE_MARGIN = timedelta(minutes=2)  # a RUNNING item untouched this long past the task's hard limit is orphaned


def build_session_factory(settings: Settings) -> SessionFactory:
    return create_session_factory(create_db_engine(settings.require_db(), application_name=settings.service_name))


def build_units(sessions: SessionFactory, clock: Clock) -> UnitOfWorkFactory:
    return lambda: SqlUnitOfWork(sessions, clock, producer=PRODUCER)


def stale_after(settings: Settings) -> timedelta:
    return settings.require_celery().time_limit + STALE_MARGIN


def build_accept_service(settings: Settings, sessions: SessionFactory, celery_app: Celery) -> AcceptConversionService:
    clock = SystemClock()
    return AcceptConversionService(
        build_units(sessions, clock),
        CeleryTaskQueue(celery_app),
        RoutePlanner(),
        clock,
        stale_after=stale_after(settings),
    )


def build_process_service(settings: Settings, sessions: SessionFactory) -> ProcessConversionService:
    clock, storage = SystemClock(), settings.require_storage()
    objects = S3ObjectStorage(build_s3_client(storage), max_input_size=int(storage.max_input_size))
    return ProcessConversionService(
        build_units(sessions, clock),
        objects,
        PdfiumFileInspector(),
        build_job_runner(settings, objects),
        clock,
        buckets=OutputBuckets(outputs=storage.bucket_outputs, derived=storage.bucket_derived),
        stale_after=stale_after(settings),
    )


def build_s3_client(storage: StorageSettings) -> "S3Client":
    timeout = storage.timeout.total_seconds()
    return boto3.client(
        "s3",
        endpoint_url=str(storage.endpoint),
        aws_access_key_id=storage.access_key.get_secret_value(),
        aws_secret_access_key=storage.secret_key.get_secret_value(),
        region_name=storage.region,
        config=Config(
            s3={"addressing_style": "path"},  # MinIO serves buckets as paths, not subdomains
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def build_rabbit_broker(settings: Settings) -> RabbitBroker:
    rabbit = settings.require_rabbit()
    return RabbitBroker(rabbit.url.get_secret_value(), app_id=settings.service_name, graceful_timeout=30)


def build_outbox_relay(settings: Settings, sessions: SessionFactory, broker: RabbitBroker) -> OutboxRelay:
    return OutboxRelay(
        sessions,
        RabbitEventPublisher(broker),
        settings.outbox,
        SystemClock(),
        instance_id=f"{socket.gethostname()}:{os.getpid()}",
    )
