"""Probe endpoints every API process exposes (standard DPL-03, IDP-01 ch. 3.2).

``/health/live`` says the process is up and does no dependency checks; ``/health/ready`` says it can take work: Redis
answers (the model queue fails closed without it). The model server is not checked: when it is down the breaker
answers AIG-503-MODEL-001 at once, and taking the gateway out of rotation would not help. Both are public by design
(SEC-02): the platform calls them without a token.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    """Body of both probes. ``checks`` lists each dependency's state."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={"examples": [{"status": "UP"}]})

    status: Literal["UP", "DOWN"]
    checks: dict[str, Literal["UP", "DOWN"]] = Field(default_factory=dict, max_length=20)


@router.get("/live", summary="Process is running", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def get_liveness() -> HealthResponse:
    return HealthResponse(status="UP")


@router.get("/ready", summary="Process can take work", response_model=HealthResponse, responses={503: {}})
async def get_readiness(request: Request, response: Response) -> HealthResponse:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="DOWN", checks={"redis": "DOWN"})
    try:
        await redis.ping()
    except (RedisError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="DOWN", checks={"redis": "DOWN"})
    return HealthResponse(status="UP", checks={"redis": "UP"})
