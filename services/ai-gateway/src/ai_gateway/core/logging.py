"""Structured logging to stdout through structlog, and the correlation id bound at the edge (OBS-01 to OBS-03).

Log an event name plus key-values, never an f-string::

    log.info("field_read_started", task="field-read", account_id=str(account_id))

Standard-library and Uvicorn loggers are routed through the same renderer, so every line has one shape.
"""

import logging
import re
import sys

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.types import Processor

from ai_gateway.core.config import LoggingSettings
from ai_gateway.core.ids import new_id

CORRELATION_ID_HEADER = "X-Correlation-Id"
CORRELATION_ID_KEY = "correlation_id"
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")  # the format the gateway creates (IDP-01 ch. 2.7)
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(settings: LoggingSettings) -> None:
    """Send structlog and standard-library records to stdout as JSON (or readable lines when local)."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # correlation_id, job_item_id, ... bound at the edge (OBS-03)
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    final: list[Processor] = (
        [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
        if settings.format == "json"
        else [structlog.dev.ConsoleRenderer()]
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.level)
    for name in _SERVER_LOGGERS:  # Uvicorn installs its own handlers; hand its records to ours instead
        server_logger = logging.getLogger(name)
        server_logger.handlers = []
        server_logger.propagate = True


def get_correlation_id() -> str | None:
    """The correlation id of the request being handled, or ``None`` outside a request."""
    value = structlog.contextvars.get_contextvars().get(CORRELATION_ID_KEY)
    return value if isinstance(value, str) else None


class CorrelationIdMiddleware:
    """Keep the caller's ``X-Correlation-Id`` (or make one), bind it to every log line and echo it back.

    The gateway creates the id when a client sends none (IDP-00 ch. 7.3); a missing or malformed one is replaced
    here so that logs and problem details always carry a usable id.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(CORRELATION_ID_HEADER, "")
        correlation_id = incoming if _VALID_CORRELATION_ID.fullmatch(incoming) else str(new_id())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**{CORRELATION_ID_KEY: correlation_id})

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        await self._app(scope, receive, send_with_correlation_id)
