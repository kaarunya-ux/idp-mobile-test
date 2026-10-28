"""Message consumer entry point, the ``intake`` process (IDP-03 ch. 2.2):

    faststream run extraction.consumers:create_app --factory

It records each ``ExtractionRequested`` and ``DeepAnalysisRequested`` and queues the work; it never reads a document.
On ``AccountDeleted`` it archives the account's templates and deletes its template samples.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog
from faststream import FastStream

from extraction.api_wiring import s3_client
from extraction.bootstrap import build_accept_service, build_rabbit_broker, build_session_factory
from extraction.core.clock import SystemClock
from extraction.core.config import Settings, get_settings
from extraction.core.db import create_async_db_engine, create_async_session_factory
from extraction.core.logging import configure_logging
from extraction.pipeline.infrastructure.messaging.consumer import declare_topology, register_consumers
from extraction.pipeline.infrastructure.tasks import create_celery_app
from extraction.template.application.services.template_service import TemplateService
from extraction.template.infrastructure.designer_adapters import S3SampleStorage
from extraction.template.infrastructure.repository import SqlTemplateUnitOfWork

log = structlog.get_logger(__name__)


def _account_deleted(settings: Settings) -> Callable[[UUID], Awaitable[None]]:
    """``AccountDeleted``: archive the account's templates and document types, then delete its template samples.
    Templates are archived, never deleted: results refer to them (IDP-03 ch. 2.10)."""
    clock = SystemClock()
    engine = create_async_db_engine(settings.require_db(), application_name=settings.service_name)
    sessions = create_async_session_factory(engine)
    templates = TemplateService(lambda: SqlTemplateUnitOfWork(sessions, clock), clock)
    storage = settings.require_storage()
    samples = S3SampleStorage(s3_client(storage), bucket=storage.bucket_templates)

    async def handle(account_id: UUID) -> None:
        archived = await templates.archive_account(account_id)
        deleted = await samples.delete_account(account_id)
        log.info("account_templates_archived", account_id=str(account_id), templates=archived, sample_files=deleted)

    return handle


def create_app(settings: Settings | None = None) -> FastStream:
    settings = settings or get_settings()
    configure_logging(settings.logging)
    broker = build_rabbit_broker(settings)
    service = build_accept_service(
        settings, build_session_factory(settings), create_celery_app(settings.require_celery())
    )
    register_consumers(
        broker,
        service,
        settings.require_rabbit(),
        derived_bucket=settings.require_storage().bucket_derived,
        on_account_deleted=_account_deleted(settings),
    )
    app = FastStream(broker)

    @app.after_startup
    async def declare_dead_letter_and_retry_queues() -> None:
        await declare_topology(broker)

    return app
