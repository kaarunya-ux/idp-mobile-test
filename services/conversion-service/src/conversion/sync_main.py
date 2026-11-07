"""The ``conversion-sync`` process: files converted while the caller waits, a photo read through ai-gateway (ADR 0002).

Run with ``uvicorn --factory conversion.sync_main:create_sync_app --port 8004``, from the worker image: it has the
converters and LibreOffice. Each request runs in a worker process; at most ``SYNC__MAX_CONCURRENT`` at once, and one
more answers 429 (send it as a job).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from conversion import health
from conversion.conversion.api import routes
from conversion.conversion.application.services.convert_now import ConvertNowRunner
from conversion.core.clock import Clock, SystemClock
from conversion.core.config import Settings, get_settings
from conversion.core.logging import CorrelationIdMiddleware, configure_logging
from conversion.core.problem_details import register_exception_handlers
from conversion.core.security import TokenVerifier
from conversion.sync_worker import ProcessConversions

log = structlog.get_logger(__name__)


def create_sync_app(
    clock: Clock | None = None, settings: Settings | None = None, runner: ConvertNowRunner | None = None
) -> FastAPI:
    """Build the process (a factory, so importing the module starts nothing). ``runner`` is a parameter so tests can
    run the route without worker processes (TST-05)."""
    settings = settings or get_settings()
    pool = None if runner is not None else ProcessConversions(settings.sync.max_concurrent)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.logging)
        if pool is not None:
            await pool.warm_up()
        log.info("sync_api_started", service=settings.service_name, environment=settings.environment)
        yield
        if pool is not None:
            pool.close()
        log.info("sync_api_stopped", service=settings.service_name)

    app = FastAPI(title="conversion-sync", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, clock or SystemClock())
    app.state.token_verifier = TokenVerifier(settings.auth)
    app.state.convert_now = runner or pool
    app.state.sync_admission = routes.Admission(settings.sync.max_concurrent)
    app.state.sync_limits = routes.UploadLimits(
        max_file_size=int(settings.sync.max_file_size),
        max_total_size=int(settings.sync.max_total_size),
        max_files=settings.sync.max_files,
    )
    app.state.sync_require_token = settings.sync.require_token
    app.state.sync_default_account = settings.sync.default_account_id
    app.include_router(health.router)
    app.include_router(routes.router)
    return app
