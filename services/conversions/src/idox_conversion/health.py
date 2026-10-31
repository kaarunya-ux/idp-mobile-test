"""Probe endpoints for the platform (standard DPL-03, IDP-03 ch. 3.2).

``/health/live`` says the process is up and does no dependency checks; ``/health/ready`` says it can take work. The
model server and the loaded converters join the readiness check with the steps that add them.
"""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    """Body of both probes. ``checks`` lists each dependency's state once there are dependencies."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={"examples": [{"status": "UP"}]})

    status: Literal["UP", "DOWN"]
    checks: dict[str, Literal["UP", "DOWN"]] = Field(default_factory=dict, max_length=20)


@router.get("/live", summary="Process is running", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def get_liveness() -> HealthResponse:
    return HealthResponse(status="UP")


@router.get("/ready", summary="Process can take work", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def get_readiness() -> HealthResponse:
    return HealthResponse(status="UP")  # the model-server and converter checks join with the steps that add them
