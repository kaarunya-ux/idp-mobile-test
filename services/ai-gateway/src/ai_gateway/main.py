"""The FastAPI application: factory, lifespan, middleware, handlers and routers (standard ch. 3.2).

Run with: ``uvicorn ai_gateway.main:app --port 8002`` (IDP-03 ch. 4.1). Internal only: there is no CORS and the Java
gateway never routes here; callers are services with their own tokens.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from ai_gateway import health
from ai_gateway.ai.api import routes as ai_routes
from ai_gateway.ai.infrastructure.metrics import publish_build_info
from ai_gateway.ai.infrastructure.model_registry import (
    ModelRegistry,
    ModelRegistryError,
    resolve_registry_path,
    verify_served_models,
)
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from ai_gateway.ai.infrastructure.prompt_history import (
    diff_prompt_files,
    read_history,
    write_history,
)
from ai_gateway.bootstrap import Container, build
from ai_gateway.core.build_info import build_stamp
from ai_gateway.core.clock import Clock, SystemClock
from ai_gateway.core.config import Settings, get_settings
from ai_gateway.core.logging import CorrelationIdMiddleware, configure_logging
from ai_gateway.core.problem_details import register_exception_handlers
from ai_gateway.core.security import TokenVerifier

log = structlog.get_logger(__name__)

type Builder = Callable[[Settings, Clock], Container]


def log_prompts_loaded(prompts: PromptFiles, history_file: Path | None = None) -> None:
    """One line per prompt in use at start-up: its version, content hash, the file it was read from (full path and
    folder) and whether a pin chose it. A change of prompt between two start-ups is visible in the logs as a different
    version or hash at a later time, with the build stamp of the environment that logged it (``logwatch.py`` turns that
    into a "prompt changed" entry). With a ``history_file``, what changed in each file since the previous start-up is
    logged as well (``prompt_file_edited``, ``_added``, ``_removed``)."""
    sources = prompts.sources()
    for prompt, pinned in prompts.loaded():
        path = sources.get(f"{prompt.label}.md")
        log.info(
            "prompt_loaded",
            prompt_version=prompt.label,
            prompt_hash=prompt.fingerprint,
            pinned=pinned,
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            file=f"{prompt.label}.md",
            path=path.path if path else None,
            folder=str(Path(path.path).parent) if path else None,
        )
    log.info("prompts_loaded", count=len(prompts.loaded()), prompts_hash=prompts.fingerprint())
    if history_file is not None:
        log_prompt_changes(prompts, history_file)


def log_prompt_changes(prompts: PromptFiles, history_file: Path) -> None:
    """Compare the prompt files with the copy kept at the previous start-up, log every difference, keep the new copy."""
    sources = prompts.sources()
    current = {name: source.text for name, source in sources.items()}
    previous = read_history(history_file)
    if previous is None:
        log.info("prompt_history_started", history_file=str(history_file), files=len(current))
    else:
        paths = {name: source.path for name, source in sources.items()}
        for change in diff_prompt_files(previous, current, paths):
            log.warning(
                f"prompt_file_{change.kind}",
                file=change.file,
                path=change.path,
                folder=change.folder,
                part=change.part,
                lines_added=change.lines_added,
                lines_removed=change.lines_removed,
                changed_share=change.changed_share,
                changes=[c.as_log() for c in change.changes],
                changes_truncated=change.truncated,
            )
    try:
        write_history(history_file, current)
    except OSError as exc:
        log.warning("prompt_history_unavailable", history_file=str(history_file), reason=type(exc).__name__)


def _load_registry(settings: Settings) -> ModelRegistry | None:
    """models.yaml, when there is one. With the check on, a missing file stops the start-up."""
    path = resolve_registry_path(settings.model.registry_file)
    if path.exists():
        return ModelRegistry.from_file(path)
    if settings.model.registry_check != "off":
        raise ModelRegistryError(f"MODEL__REGISTRY_CHECK is {settings.model.registry_check} but {path} does not exist")
    return None


def _servers_by_role(settings: Settings) -> dict[str, tuple[str, str | None]]:
    """Where each gateway role's requests go: the main server, or the page server when page-read has its own."""
    model = settings.model
    main = (str(model.base_url), model.api_key.get_secret_value() if model.api_key else None)
    page = model.page
    own = (str(page.base_url), page.api_key.get_secret_value() if page.api_key else None) if page.base_url else main
    return {"vl": main, "smart": main, "auto": main, "page": own}


def create_app(clock: Clock | None = None, settings: Settings | None = None, builder: Builder = build) -> FastAPI:
    """Build the API. The clock, settings and wiring are parameters so tests can replace them (TST-05)."""
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry = _load_registry(settings)
        prompts = PromptFiles.load(pins=settings.prompts.pins)
        stamp = build_stamp(
            settings,
            {"prompts": prompts.fingerprint(), "models": registry.fingerprint() if registry else "none"},
        )
        configure_logging(settings.logging, stamp)
        log_prompts_loaded(prompts, settings.prompts.history_file)
        publish_build_info(
            env=stamp.environment, version=stamp.version, commit=stamp.commit, config_hash=stamp.config_hash
        )
        if registry is not None:
            async with httpx.AsyncClient(timeout=5.0) as probe:
                await verify_served_models(
                    registry,
                    _servers_by_role(settings),
                    probe,
                    settings.model.registry_check,
                    wait_seconds=settings.model.registry_wait.total_seconds(),
                )
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
