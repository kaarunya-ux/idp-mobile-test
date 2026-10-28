"""The FastAPI application: factory, lifespan, middleware, handlers and routers (standard ch. 3.2).

Run with: ``uvicorn ai_gateway.main:app --port 8002`` (IDP-03 ch. 4.1). Internal only: there is no CORS and the Java
gateway never routes here; callers are services with their own tokens.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from ai_gateway import health
from ai_gateway.ai.api import routes as ai_routes
from ai_gateway.bootstrap import Container, build
from ai_gateway.core.clock import Clock, SystemClock
from ai_gateway.core.config import Settings, get_settings
from ai_gateway.core.logging import CorrelationIdMiddleware, configure_logging
from ai_gateway.core.problem_details import register_exception_handlers
from ai_gateway.core.security import TokenVerifier

log = structlog.get_logger(__name__)

type Builder = Callable[[Settings, Clock], Container]


def create_app(clock: Clock | None = None, settings: Settings | None = None, builder: Builder = build) -> FastAPI:
    """Build the API. The clock, settings and wiring are parameters so tests can replace them (TST-05)."""
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.logging)
        container = builder(settings, clock)
        app.state.ai, app.state.redis = container.ai, container.redis
        log.info("api_started", service=settings.service_name, environment=settings.environment)
        yield
        await container.close()
        log.info("api_stopped", service=settings.service_name)

    app = FastAPI(title="ai-gateway", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, clock)
    app.state.token_verifier = TokenVerifier(settings.auth)
    app.include_router(health.router)
    app.include_router(ai_routes.router)
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()
