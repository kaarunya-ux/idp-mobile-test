"""Builds the long-lived objects once per process and wires the use cases to their adapters (DI-01, DI-03).

Without ``REDIS__URL`` or ``STORAGE__*`` the AI routes answer 503 (``AIG-503-DEPENDENCY-001``) instead of the process
refusing to start, so the health probes and the OpenAPI document still work on a laptop.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import boto3
import httpx
from botocore.config import Config
from redis.asyncio import Redis

from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService
from ai_gateway.ai.domain.models import ModelRole, Plan, Task
from ai_gateway.ai.infrastructure.breaker import CircuitBreaker
from ai_gateway.ai.infrastructure.metrics import PrometheusMetrics
from ai_gateway.ai.infrastructure.model_client import OpenAiModelClient
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from ai_gateway.ai.infrastructure.redis_caps import RedisCaps
from ai_gateway.ai.infrastructure.redis_slots import RedisSlots
from ai_gateway.ai.infrastructure.s3_images import S3ImageStore
from ai_gateway.core.clock import Clock
from ai_gateway.core.config import Settings, StorageSettings
from ai_gateway.core.errors import DependencyUnavailableError
from ai_gateway.core.redis import create_redis

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@dataclass(slots=True, kw_only=True)
class AiServices:
    read_fields: ReadFieldsService
    classify: ClassifyPageService
    analyse: AnalysePageService
    suggest: SuggestTemplateService


@dataclass(slots=True, kw_only=True)
class Container:
    """What the process holds open; ``close()`` releases it on shutdown."""

    ai: "AiServices | Unavailable"
    http: httpx.AsyncClient | None = None
    redis: Redis | None = None

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()
        if self.redis is not None:
            await self.redis.aclose()


class Unavailable:
    """Stands in for the services when Redis or storage isn't configured: every route answers 503."""

    def __getattr__(self, name: str) -> Any:
        raise DependencyUnavailableError("AI gateway's Redis or storage")


def build(settings: Settings, clock: Clock) -> Container:
    if settings.redis is None or settings.storage is None:
        return Container(ai=Unavailable())
    model = settings.model
    headers = {"Authorization": f"Bearer {model.api_key.get_secret_value()}"} if model.api_key else {}
    http = httpx.AsyncClient(
        base_url=str(model.base_url).rstrip("/") + "/",
        headers=headers,
        timeout=httpx.Timeout(60, connect=model.timeouts.connect.total_seconds()),
        limits=httpx.Limits(max_connections=model.vl_max_concurrent * 4),
    )
    redis = create_redis(settings.redis)
    breaker = CircuitBreaker(
        failures=settings.breaker.failures,
        window_s=settings.breaker.window.total_seconds(),
        reset_after_s=settings.breaker.reset_after.total_seconds(),
    )
    models = {ModelRole.VL: model.vl_model, ModelRole.SMART: model.smart_model, ModelRole.AUTO: model.auto_model}
    prompts = PromptFiles.load()
    runner = ModelCallRunner(
        model=OpenAiModelClient(http, models=models, breaker=breaker),
        slots=RedisSlots(
            redis,
            capacity=model.vl_max_concurrent,
            wait_timeout_s=settings.queue.wait_timeout.total_seconds(),
            lease_s=settings.queue.lease.total_seconds(),
            priority_share=settings.queue.priority_share,
        ),
        caps=RedisCaps(redis, limits=_limits(settings)),
        prompts=prompts,
        metrics=PrometheusMetrics(),
        clock=clock,
        timeouts={
            Task.FIELD_READ: model.timeouts.field_read.total_seconds(),
            Task.CLASSIFY: model.timeouts.classify.total_seconds(),
            Task.PAGE_ANALYSIS: model.timeouts.page_analysis.total_seconds(),
            Task.TEMPLATE_SUGGEST: model.timeouts.template_suggest.total_seconds(),
        },
    )
    images = S3ImageStore(
        _s3(settings.storage),
        allowed_buckets=settings.storage.allowed_buckets,
        max_bytes=int(model.max_image_bytes),
        max_side_px=model.max_image_side_px,
    )
    services = AiServices(
        read_fields=ReadFieldsService(runner=runner, images=images, prompts=prompts),
        classify=ClassifyPageService(runner=runner, images=images, prompts=prompts),
        analyse=AnalysePageService(runner=runner, images=images, prompts=prompts),
        suggest=SuggestTemplateService(runner=runner, images=images, prompts=prompts),
    )
    return Container(ai=services, http=http, redis=redis)


def _limits(settings: Settings) -> dict[Plan, int]:
    caps = settings.caps
    return {Plan.GUEST: caps.guest, Plan.PERSONAL: caps.personal, Plan.CORPORATE: caps.corporate}


def _s3(storage: StorageSettings) -> "S3Client":
    timeout = storage.timeout.total_seconds()
    return boto3.client(
        "s3",
        endpoint_url=str(storage.endpoint),
        aws_access_key_id=storage.access_key.get_secret_value(),
        aws_secret_access_key=storage.secret_key.get_secret_value(),
        region_name=storage.region,
        config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 2}),
    )
