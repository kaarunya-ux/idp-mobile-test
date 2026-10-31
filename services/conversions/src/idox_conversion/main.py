"""The health application: factory, lifespan and router (standard ch. 3.2).

Run with: ``uvicorn idox_conversion.main:app --port 8001``. The service has no public API: work arrives as
``ConversionRequested`` messages (IDP-03 ch. 3.1), and this application only answers the platform's probes.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from idox_conversion import health
from idox_conversion.core.config import get_settings
from idox_conversion.core.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging)
    log.info("health_api_started", service=settings.service_name, environment=settings.environment)
    yield
    log.info("health_api_stopped", service=settings.service_name)


def create_app() -> FastAPI:
    """Build the probe application."""
    app = FastAPI(title="idox-conversion-service", version="0.1.0", lifespan=_lifespan)
    app.include_router(health.router)
    return app


app = create_app()
