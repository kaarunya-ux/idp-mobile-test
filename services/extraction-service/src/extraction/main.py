"""The FastAPI application: factory, lifespan, middleware, handlers and routers (standard ch. 3.2).

Run with: ``uvicorn extraction.main:app --port 8000`` (IDP-03 ch. 2.2). CORS is not enabled here: only the edge
component answers browsers (SEC-09). The API is the slim image: it wires templates and document types here, and never
imports the pipeline's OpenCV or OCR code (that wiring is ``bootstrap``, for the workers).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from extraction import health
from extraction.core.clock import Clock, SystemClock
from extraction.core.config import Settings, get_settings
from extraction.core.db import create_async_db_engine, create_async_session_factory
from extraction.core.errors import DependencyUnavailableError
from extraction.core.logging import CorrelationIdMiddleware, configure_logging
from extraction.core.problem_details import register_exception_handlers
from extraction.core.security import TokenVerifier
from extraction.template.api import routes as template_routes
from extraction.template.application.services.document_type_service import DocumentTypeService
from extraction.template.application.services.template_service import TemplateService, UnitFactory
from extraction.template.domain.ports import TemplateUnitOfWorkPort
from extraction.template.infrastructure.repository import SqlTemplateUnitOfWork

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging)
    log.info("api_started", service=settings.service_name, environment=settings.environment)
    yield
    log.info("api_stopped", service=settings.service_name)


def create_app(clock: Clock | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the API. The clock and settings are parameters so tests can pin them (TST-05)."""
    settings = settings or get_settings()
    clock = clock or SystemClock()
    app = FastAPI(title="extraction-service", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, clock)
    units = _template_units(settings, clock)
    app.state.token_verifier = TokenVerifier(settings.auth)
    app.state.template_service = TemplateService(units, clock)
    app.state.document_type_service = DocumentTypeService(units)
    app.include_router(health.router)
    app.include_router(template_routes.templates)
    app.include_router(template_routes.document_types)
    return app


def _template_units(settings: Settings, clock: Clock) -> UnitFactory:
    """Units of work on the async engine; without ``DB__URL`` every template route answers 503."""
    if settings.db is None:

        def unavailable() -> TemplateUnitOfWorkPort:
            raise DependencyUnavailableError("template database")

        return unavailable
    sessions = create_async_session_factory(create_async_db_engine(settings.db, application_name=settings.service_name))
    return lambda: SqlTemplateUnitOfWork(sessions, clock)


app = create_app()
