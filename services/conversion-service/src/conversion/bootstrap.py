"""Composition root: builds the long-lived objects once per process (DI-01, DI-03).

The only module allowed to import from every layer. Each entry point (``consumers``, ``worker``,
``outbox_relay``) builds what it needs here, so there is one wiring for all of them.
"""

import os
import socket
from datetime import timedelta
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from celery import Celery
from faststream.rabbit import RabbitBroker

from conversion.conversion.application.services.accept_conversion import AcceptConversionService
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.application.services.edit_pdf import EditHandler
from conversion.conversion.application.services.export_results import ExportHandler
from conversion.conversion.application.services.process_conversion import OutputBuckets, ProcessConversionService
from conversion.conversion.application.services.run_job import (
    ConvertHandler,
    JobRunner,
    MergeHandler,
    PreviewHandler,
)
from conversion.conversion.domain.ports import ObjectStoragePort, UnitOfWorkFactory
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.domain.values import JobType, ThumbnailSpec
from conversion.conversion.infrastructure.converters.docx_to_model import PythonDocxReader
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter
from conversion.conversion.infrastructure.converters.images_to_pdf import Img2PdfImageWrapper
from conversion.conversion.infrastructure.converters.model_to_docx import PythonDocxWriter
from conversion.conversion.infrastructure.converters.model_to_pptx import PythonPptxWriter
from conversion.conversion.infrastructure.converters.model_to_sheets import CsvModelWriter, OpenpyxlModelWriter
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
from conversion.conversion.infrastructure.registry import ConverterRegistry
from conversion.conversion.infrastructure.secrets import RsaSecrets
from conversion.conversion.infrastructure.storage import S3ObjectStorage
from conversion.conversion.infrastructure.tasks import CeleryTaskQueue
from conversion.core.clock import Clock, SystemClock
from conversion.core.config import Settings, StorageSettings
from conversion.core.db import SessionFactory, create_db_engine, create_session_factory
from conversion.core.outbox import OutboxRelay

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client


def build_converter_registry(settings: Settings) -> ConverterRegistry:
    """Every converter this build of the service can run."""
    limits, office = settings.limits, settings.office
    return ConverterRegistry(
        [
            PillowImageFormatConverter(max_pixels=limits.max_image_pixels),
            PdfiumPageRenderer(max_pages=limits.max_pages, max_side_px=limits.max_render_side_px),
            Img2PdfImageWrapper(max_pixels=limits.max_image_pixels),
            PptxImageSlides(max_pixels=limits.max_image_pixels),
            LibreOfficePdfConverter(binary=office.binary, timeout=office.timeout),
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


def build_convert_file_service(settings: Settings) -> ConvertFileService:
    return ConvertFileService(RoutePlanner(), build_converter_registry(settings))


def build_job_runner(settings: Settings, storage: ObjectStoragePort) -> JobRunner:
    limits, preview, edit = settings.limits, settings.preview, settings.edit
    converter = build_convert_file_service(settings)
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
