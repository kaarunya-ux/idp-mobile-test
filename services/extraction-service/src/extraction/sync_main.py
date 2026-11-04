"""The ``extraction-sync`` process: the synchronous extract (ADR 0009), with the OCR engines loaded once.

Run with ``uvicorn --factory extraction.sync_main:create_sync_app --port 8003``, from the worker image: it has OpenCV,
ONNX Runtime and the models. The slim API process can't run OCR (ML-09), so this route lives in its own process. Each
request runs the pipeline in a worker thread; at most ``QUICK__MAX_CONCURRENT`` at once, and one more answers 429
(send it as a job).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from extraction import health
from extraction.bootstrap import build_quick_extract, build_session_factory
from extraction.core.clock import Clock, SystemClock
from extraction.core.config import Settings, get_settings
from extraction.core.logging import CorrelationIdMiddleware, configure_logging
from extraction.core.problem_details import register_exception_handlers
from extraction.core.security import TokenVerifier
from extraction.quick.api import routes as quick_routes
from extraction.quick.application.quick_extract import QuickExtractService

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging)
    log.info("sync_api_started", service=settings.service_name, environment=settings.environment)
    yield
    log.info("sync_api_stopped", service=settings.service_name)


def create_sync_app(
    clock: Clock | None = None, settings: Settings | None = None, service: QuickExtractService | None = None
) -> FastAPI:
    """Build the process (a factory, so importing the module loads no OCR engines). ``service`` is a parameter so
    tests can run it without OCR engines or storage (TST-05)."""
    settings = settings or get_settings()
    app = FastAPI(title="extraction-sync", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, clock or SystemClock())
    app.state.token_verifier = TokenVerifier(settings.auth)
    app.state.quick_extract_service = service or build_quick_extract(settings, build_session_factory(settings))
    app.state.quick_admission = quick_routes.Admission(settings.quick.max_concurrent)
    quick = settings.quick
    app.state.quick_tokens = quick_routes.TokenPolicy(
        required=quick.require_token, default_account_id=quick.default_account_id, default_plan=quick.default_plan
    )
    app.include_router(health.router)
    app.include_router(quick_routes.router)
    app.mount("/metrics", make_asgi_app())
    return app
