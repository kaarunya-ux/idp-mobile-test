"""Wiring for the API and intake processes that must stay slim: no OpenCV, OCR or models (ML-09).

``bootstrap`` builds the workers and loads the OCR engines, so the API can't import it. What the template designer
needs in the API (Celery to send tasks, MinIO to read prepared samples, ai-gateway for suggestions) is built here.
A missing piece answers 503 when it is used, instead of stopping the process.
"""

from typing import TYPE_CHECKING, Any

import boto3
import httpx
from botocore.config import Config
from celery import Celery

from extraction.core.clock import Clock
from extraction.core.config import Settings, StorageSettings
from extraction.core.errors import DependencyUnavailableError
from extraction.core.service_auth import ServiceTokenProvider
from extraction.pipeline.infrastructure.ai.gateway_reader import AiGatewayClient
from extraction.template.application.services.designer_service import DesignerService
from extraction.template.application.services.template_service import UnitFactory
from extraction.template.infrastructure.designer_adapters import (
    CeleryDesignerTasks,
    GatewayTemplateSuggester,
    S3SampleStorage,
)

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client


class _Unavailable:
    """Stands in for a port whose settings are missing: any use answers 503."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str) -> Any:
        raise DependencyUnavailableError(self._what)


def build_designer(settings: Settings, units: UnitFactory, clock: Clock) -> DesignerService:
    tasks: Any = (
        CeleryDesignerTasks(Celery("extraction", broker=settings.celery.broker_url.get_secret_value()))
        if settings.celery is not None
        else _Unavailable("task queue")
    )
    storage: Any = (
        S3SampleStorage(s3_client(settings.storage), bucket=settings.storage.bucket_templates)
        if settings.storage is not None
        else _Unavailable("sample storage")
    )
    return DesignerService(
        units,
        clock,
        tasks=tasks,
        storage=storage,
        suggester=GatewayTemplateSuggester(gateway_client(settings)),
        bundle_thresholds=bundle_thresholds(settings),
    )


def gateway_client(settings: Settings) -> AiGatewayClient | None:
    if settings.ai_gateway.url is None or settings.service_auth is None:
        return None
    auth = settings.service_auth
    tokens = ServiceTokenProvider(httpx.Client(timeout=auth.timeout.total_seconds()), auth)
    seconds = settings.ai_gateway.timeout.total_seconds()
    client = httpx.Client(
        base_url=str(settings.ai_gateway.url).rstrip("/"), timeout=httpx.Timeout(seconds, connect=min(2.0, seconds))
    )
    return AiGatewayClient(client, tokens)


def bundle_thresholds(settings: Settings) -> dict[str, Any]:
    """The numbers the phone must use (IDP-04: the same quality gate as the server), from settings (ML-07)."""
    quality = settings.quality
    return {
        "quality": {
            "blurMin": quality.blur_min,
            "brightnessRange": list(quality.brightness_range),
            "glareMax": quality.glare_max,
            "minShortSidePx": quality.min_short_side_px,
        },
        "verify": {"nameSimilarity": settings.verify.name_similarity},
        "review": {"confidenceThreshold": settings.review.confidence_threshold},
    }


def s3_client(storage: StorageSettings) -> "S3Client":
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
