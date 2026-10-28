"""Composition root: builds the long-lived objects once per process (DI-01, DI-03).

The only module allowed to import from every layer. Each entry point (``consumers``, ``worker``,
``outbox_relay``) builds what it needs here. The OCR engines and models load once per worker process; the light
pipeline services are built per run, with that run's progress reporter.
"""

import os
import socket
from collections.abc import Sequence
from datetime import timedelta
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import httpx
from botocore.config import Config
from celery import Celery
from faststream.rabbit import RabbitBroker

from extraction.core.clock import Clock, SystemClock
from extraction.core.config import MissingSettingsError, ServiceAuthSettings, Settings, StorageSettings
from extraction.core.db import SessionFactory, create_db_engine, create_session_factory
from extraction.core.outbox import OutboxRelay
from extraction.core.service_auth import ServiceTokenProvider
from extraction.pipeline.application.services.accept_extraction import AcceptExtractionService
from extraction.pipeline.application.services.extract_document import AiAssist, ExtractDocumentService
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.decisions import Thresholds
from extraction.pipeline.domain.ports import ProgressReporterPort, UnitOfWorkFactory
from extraction.pipeline.domain.quality import QualityThresholds
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.pipeline.infrastructure.ai.gateway_reader import GatewayFieldReader
from extraction.pipeline.infrastructure.ai.vision_reader import VisionModelFieldReader
from extraction.pipeline.infrastructure.digital.pdf_text_reader import PdfiumTextReader
from extraction.pipeline.infrastructure.messaging.messages import PRODUCER
from extraction.pipeline.infrastructure.messaging.publisher import RabbitEventPublisher
from extraction.pipeline.infrastructure.persistence.repository import SqlUnitOfWork
from extraction.pipeline.infrastructure.persistence.template_catalog import SqlTemplateCatalog
from extraction.pipeline.infrastructure.preprocess.opencv_preprocessor import OpenCvPagePreparer
from extraction.pipeline.infrastructure.preprocess.orientation import DocumentOrientationClassifier
from extraction.pipeline.infrastructure.quality.opencv_quality_gate import OpenCvQualityAssessor
from extraction.pipeline.infrastructure.quality.page_loader import DocumentPageLoader
from extraction.pipeline.infrastructure.storage.s3_storage import S3ObjectStorage
from extraction.pipeline.infrastructure.tasks import CeleryTaskQueue
from idp_ocr_engines import ModelRegistry, RuntimeLimits
from idp_ocr_engines.engines.ppocr import PpOcrEngine, PpOcrModels
from idp_ocr_engines.port import OcrEnginePort

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client

STALE_MARGIN = timedelta(minutes=2)  # a RUNNING item untouched this long past the task's hard limit is orphaned
# Slot A reads English with PP-OCRv6; slot B reads English and Hindi with PP-OCRv5 (Sumukh's benchmark choice).
# Slot C is empty while Indra is parked (extraction-service ADR 0002).
_ENGINE_LANGUAGES = {"A": ("en",), "B": ("en", "hi")}


def pipeline_version() -> str:
    try:
        return metadata.version("extraction-service")
    except metadata.PackageNotFoundError:
        return "0.0.0"


class PipelineServices:
    """``PipelineFactory``: the engines and models, loaded once; services built per run."""

    def __init__(self, settings: Settings, clock: Clock, catalog: SqlTemplateCatalog) -> None:
        self._catalog = catalog
        registry = ModelRegistry.from_file(Path(settings.ocr.models_file))
        limits = RuntimeLimits(threads=settings.ocr.threads)
        self._settings, self._clock = settings, clock
        self._engines = _engines(settings, registry, limits)
        self._loader = DocumentPageLoader(
            max_file_bytes=settings.limits.max_file_size,
            max_pages=settings.limits.max_pages,
            max_pixels=settings.limits.max_image_pixels,
            pdf_dpi=settings.limits.pdf_dpi,
        )
        quality = settings.quality
        self._assessor = OpenCvQualityAssessor(
            min_card_area_fraction=settings.prepare.min_card_area_fraction,
            saturation_level=quality.saturation_level,
            glare_soft_edge_gradient=quality.glare_soft_edge_gradient,
        )
        self._preparer = OpenCvPagePreparer(
            orientation=DocumentOrientationClassifier.from_registry(
                registry=registry, models_dir=settings.ocr.models_dir, model_name="doc-orientation", limits=limits
            ),
            min_card_area_fraction=settings.prepare.min_card_area_fraction,
            orientation_min_confidence=settings.prepare.orientation_min_confidence,
        )
        self._thresholds = QualityThresholds(
            blur_min=quality.blur_min,
            brightness_min=quality.brightness_range[0],
            brightness_max=quality.brightness_range[1],
            glare_max=quality.glare_max,
            min_short_side_px=quality.min_short_side_px,
            require_document_edges=quality.require_document_edges,
        )
        self._ai = build_ai_assist(settings)
        self._digital = PdfiumTextReader(dpi=settings.limits.pdf_dpi)

    @property
    def digital_reader(self) -> PdfiumTextReader:
        return self._digital

    def prepare_service(self, progress: ProgressReporterPort) -> PrepareDocumentService:
        return PrepareDocumentService(
            loader=self._loader,
            assessor=self._assessor,
            preparer=self._preparer,
            thresholds=self._thresholds,
            progress=progress,
        )

    @property
    def catalog(self) -> SqlTemplateCatalog:
        return self._catalog

    def extract_service(
        self, progress: ProgressReporterPort, templates: Sequence[TemplateDefinition]
    ) -> ExtractDocumentService:
        return ExtractDocumentService(
            engines=self._engines,
            languages=_ENGINE_LANGUAGES,
            templates=templates,
            thresholds=Thresholds(
                name_similarity=self._settings.verify.name_similarity,
                review_confidence=self._settings.review.confidence_threshold,
            ),
            clock=self._clock,
            pipeline_version=pipeline_version(),
            progress=progress,
            ai=self._ai,
        )


def _engines(settings: Settings, registry: ModelRegistry, limits: RuntimeLimits) -> dict[str, OcrEnginePort]:
    def engine(slot: str, detector: str, recognizer: str, extra: dict[str, str] | None = None) -> PpOcrEngine:
        return PpOcrEngine.from_registry(
            engine_id=slot,
            registry=registry,
            models_dir=settings.ocr.models_dir,
            models=PpOcrModels(detector=detector, recognizer=recognizer, extra_recognizers=extra or {}),
            limits=limits,
        )

    return {
        "A": engine("ocr-a", "ppocrv6-small-det", "ppocrv6-small-rec"),
        "B": engine("ocr-b", "ppocrv5-mobile-det", "ppocrv5-mobile-rec", {"hi": "ppocrv5-devanagari-rec"}),
    }


def build_ai_assist(settings: Settings) -> AiAssist | None:
    """The AI fallback: ai-gateway when ``AI_GATEWAY__URL`` is set; on a laptop without it, the model host directly
    (``AI_FALLBACK__URL``, local only, ADR 0006); otherwise None (unsettled fields then stay NEEDS_REVIEW)."""
    gateway = settings.ai_gateway
    if gateway.url is not None:
        auth = _required_service_auth(settings)
        tokens = ServiceTokenProvider(httpx.Client(timeout=auth.timeout.total_seconds()), auth)
        seconds = gateway.timeout.total_seconds()
        client = httpx.Client(
            base_url=str(gateway.url).rstrip("/"), timeout=httpx.Timeout(seconds, connect=min(2.0, seconds))
        )
        return AiAssist(reader=GatewayFieldReader(client, tokens), confidence=gateway.confidence)
    ai = settings.ai_fallback
    if ai.url is None or settings.environment != "local":
        return None
    headers = {"Authorization": f"Bearer {ai.api_key.get_secret_value()}"} if ai.api_key else {}
    seconds = ai.timeout.total_seconds()
    client = httpx.Client(
        base_url=str(ai.url).rstrip("/"), headers=headers, timeout=httpx.Timeout(seconds, connect=min(5.0, seconds))
    )
    return AiAssist(
        reader=VisionModelFieldReader(client, model=ai.model, max_edge_px=ai.max_edge_px), confidence=ai.confidence
    )


def _required_service_auth(settings: Settings) -> ServiceAuthSettings:
    if settings.service_auth is None:
        raise MissingSettingsError("AI_GATEWAY__URL needs SERVICE_AUTH__TOKEN_URL and SERVICE_AUTH__CLIENT_SECRET")
    return settings.service_auth


def build_session_factory(settings: Settings) -> SessionFactory:
    return create_session_factory(create_db_engine(settings.require_db(), application_name=settings.service_name))


def build_units(sessions: SessionFactory, clock: Clock) -> UnitOfWorkFactory:
    return lambda: SqlUnitOfWork(sessions, clock, producer=PRODUCER)


def stale_after(settings: Settings) -> timedelta:
    return settings.require_celery().time_limit + STALE_MARGIN


def build_accept_service(settings: Settings, sessions: SessionFactory, celery_app: Celery) -> AcceptExtractionService:
    clock = SystemClock()
    return AcceptExtractionService(
        build_units(sessions, clock), CeleryTaskQueue(celery_app), clock, stale_after=stale_after(settings)
    )


def build_run_service(settings: Settings, sessions: SessionFactory) -> RunExtractionService:
    clock, storage = SystemClock(), settings.require_storage()
    objects = S3ObjectStorage(build_s3_client(storage), max_input_size=int(storage.max_input_size))
    return RunExtractionService(
        build_units(sessions, clock),
        objects,
        objects,
        PipelineServices(settings, clock, SqlTemplateCatalog(sessions)),
        clock,
        derived_bucket=storage.bucket_derived,
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
