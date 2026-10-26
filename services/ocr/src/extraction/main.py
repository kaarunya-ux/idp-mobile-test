"""The FastAPI application: factory, lifespan, middleware, handlers and routers (standard ch. 3.2).

Run with: ``uvicorn extraction.main:app --port 8000`` (IDP-03 ch. 2.2). CORS is not enabled here: only the edge
component answers browsers (SEC-09).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from extraction import health
from extraction.core.clock import Clock, SystemClock
from extraction.core.config import get_settings
from extraction.core.logging import CorrelationIdMiddleware, configure_logging
from extraction.core.problem_details import register_exception_handlers

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging)
    log.info("api_started", service=settings.service_name, environment=settings.environment)
    yield
    log.info("api_stopped", service=settings.service_name)


def create_app(clock: Clock | None = None) -> FastAPI:
    """Build the API. The clock is a parameter so tests can pin it (TST-05)."""
    app = FastAPI(title="extraction-service", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, clock or SystemClock())
    app.include_router(health.router)
    return app


app = create_app()
