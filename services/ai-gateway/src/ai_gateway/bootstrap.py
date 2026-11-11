"""Builds the long-lived objects once per process and wires the use cases to their adapters (DI-01, DI-03).

Without ``REDIS__URL`` or ``STORAGE__*`` the AI routes answer 503 (``AIG-503-DEPENDENCY-001``) instead of the process
refusing to start, so the health probes and the OpenAPI document still work on a laptop.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import boto3
import httpx
from botocore.config import Config
from pydantic import SecretStr
from redis.asyncio import Redis

from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_document import ReadDocumentService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
from ai_gateway.ai.application.services.read_layout import ReadLayoutService
from ai_gateway.ai.application.services.read_page import ReadPageService
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService
from ai_gateway.ai.domain.models import ModelRole, Plan, Task
from ai_gateway.ai.domain.ports import ModelPort
from ai_gateway.ai.infrastructure.breaker import CircuitBreaker
from ai_gateway.ai.infrastructure.metrics import PrometheusMetrics
from ai_gateway.ai.infrastructure.model_client import OpenAiModelClient, RoutedModel
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
    read_page: ReadPageService
    read_document: ReadDocumentService
    read_layout: ReadLayoutService


@dataclass(slots=True, kw_only=True)
class Container:
    """What the process holds open; ``close()`` releases it on shutdown."""

    ai: "AiServices | Unavailable"
    http: httpx.AsyncClient | None = None
    redis: Redis | None = None
    page_http: httpx.AsyncClient | None = None  # the page model's own server, when it has one (ADR 0005)

    async def close(self) -> None:
        for client in (self.http, self.page_http):
            if client is not None:
                await client.aclose()
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
    model_port, http, page_http = _model_servers(settings)
    redis = create_redis(settings.redis)
    prompts = PromptFiles.load(pins=settings.prompts.pins)
    runner = ModelCallRunner(
        model=model_port,
        slots=RedisSlots(
            redis,
            capacity=model.vl_max_concurrent,
            wait_timeout_s=settings.queue.wait_timeout.total_seconds(),
            lease_s=settings.queue.lease.total_seconds(),
            priority_share=settings.queue.priority_share,
            interactive_reserved=settings.queue.interactive_reserved_slots,
            interactive_wait_timeout_s=settings.queue.interactive_wait_timeout.total_seconds(),
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
            Task.PAGE_READ: model.timeouts.page_read.total_seconds(),
            Task.PAGE_LAYOUT: model.timeouts.page_layout.total_seconds(),
            Task.GENERIC_READ: model.timeouts.generic_read.total_seconds(),
        },
    )
    images = S3ImageStore(
        _s3(settings.storage),
        allowed_buckets=settings.storage.allowed_buckets,
        max_bytes=int(model.max_image_bytes),
        max_side_px=model.max_image_side_px,
        min_side_px=model.min_image_side_px,
    )
    services = AiServices(
        read_fields=ReadFieldsService(runner=runner, images=images, prompts=prompts),
        classify=ClassifyPageService(runner=runner, images=images, prompts=prompts),
        analyse=AnalysePageService(runner=runner, images=images, prompts=prompts),
        suggest=SuggestTemplateService(runner=runner, images=images, prompts=prompts),
        read_page=ReadPageService(
            runner=runner,
            images=images,
            prompts=prompts,
            max_side_px=model.page_read_max_side_px,
            min_block_share=model.page_read_min_block_share,
        ),
        read_document=ReadDocumentService(runner=runner, images=images, prompts=prompts),
        read_layout=ReadLayoutService(
            runner=runner, images=images, prompts=prompts, max_side_px=model.page_read_max_side_px
        ),
    )
    return Container(ai=services, http=http, redis=redis, page_http=page_http)


def _model_servers(settings: Settings) -> tuple[ModelPort, httpx.AsyncClient, httpx.AsyncClient | None]:
    """The model server, and the page model's own server when ``MODEL__PAGE__BASE_URL`` is set (ADR 0005). Without
    one, the page role is the main server's vision model."""
    model = settings.model
    http = _http(str(model.base_url), model.api_key, settings)
    names = {
        ModelRole.VL: model.vl_model,
        ModelRole.SMART: model.smart_model,
        ModelRole.AUTO: model.auto_model,
        ModelRole.PAGE: model.vl_model,
    }
    main = OpenAiModelClient(
        http, models=names, breaker=_breaker(settings), send_schema=model.send_schema, thinking=model.thinking
    )
    page = model.page
    if page.base_url is None:
        return main, http, None
    page_http = _http(str(page.base_url), page.api_key, settings)
    page_model = OpenAiModelClient(
        page_http,
        models={ModelRole.PAGE: page.model},
        breaker=_breaker(settings),
        send_schema=page.send_schema,
        thinking=model.thinking,
    )
    return RoutedModel(main, {ModelRole.PAGE: page_model}), http, page_http


def _http(base_url: str, api_key: SecretStr | None, settings: Settings) -> httpx.AsyncClient:
    model = settings.model
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {api_key.get_secret_value()}"} if api_key else {},
        timeout=httpx.Timeout(60, connect=model.timeouts.connect.total_seconds()),
        limits=httpx.Limits(max_connections=model.vl_max_concurrent * 4),
    )


def _breaker(settings: Settings) -> CircuitBreaker:
    """One per server: a failing page model doesn't stop field reading on the card model, nor the other way round."""
    return CircuitBreaker(
        failures=settings.breaker.failures,
        window_s=settings.breaker.window.total_seconds(),
        reset_after_s=settings.breaker.reset_after.total_seconds(),
    )


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
