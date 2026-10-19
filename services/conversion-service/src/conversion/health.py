"""Probe-only HTTP endpoints on port 8001 (IDP-03 ch. 3.2, standard DPL-03).

conversion-service has no public API: work arrives as messages. These two routes tell the platform that the
process is up (``/health/live``) and able to take work (``/health/ready``). Both are public by design (SEC-02).

Run with: ``uvicorn conversion.health:app --port 8001``
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, status
from pydantic import BaseModel, ConfigDict, Field

from conversion.core.config import get_settings
from conversion.core.logging import configure_logging

log = structlog.get_logger(__name__)


class HealthResponse(BaseModel):
    """Body of both probes. ``checks`` lists each dependency's state once there are dependencies (step 4)."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={"examples": [{"status": "UP"}]})

    status: Literal["UP", "DOWN"]
    checks: dict[str, Literal["UP", "DOWN"]] = Field(default_factory=dict, max_length=20)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.logging)
    log.info("health_server_started", service=settings.service_name, environment=settings.environment)
    yield
    log.info("health_server_stopped", service=settings.service_name)


def create_app() -> FastAPI:
    """Build the probe app. API docs are switched off: there is no API to document."""
    app = FastAPI(
        title="conversion-service health",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get(
        "/health/live",
        summary="Process is running",
        response_model=HealthResponse,
        status_code=status.HTTP_200_OK,
    )
    async def get_liveness() -> HealthResponse:
        return HealthResponse(status="UP")

    @app.get(
        "/health/ready",
        summary="Process can take work",
        response_model=HealthResponse,
        status_code=status.HTTP_200_OK,
    )
    async def get_readiness() -> HealthResponse:
        return HealthResponse(status="UP")  # RabbitMQ and storage checks join in step 4

    return app


app = create_app()
