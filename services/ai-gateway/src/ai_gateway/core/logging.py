"""Structured logging to stdout through structlog, and the correlation id bound at the edge (OBS-01 to OBS-03).

Log an event name plus key-values, never an f-string::

    log.info("field_read_started", task="field-read", account_id=str(account_id))

Standard-library and Uvicorn loggers are routed through the same renderer, so every line has one shape.
"""

import logging
import re
import sys
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.types import EventDict, Processor, WrappedLogger

from ai_gateway.core.build_info import BuildStamp
from ai_gateway.core.config import LoggingSettings
from ai_gateway.core.ids import new_id

CORRELATION_ID_HEADER = "X-Correlation-Id"
CORRELATION_ID_KEY = "correlation_id"
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")  # the format the gateway creates (IDP-01 ch. 2.7)
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


# Shapes of Indian personal data. OBS-04 says never to log it; this is the safety net for a value that slips into a
# log line (a library error message that quotes a cell, for example). The first matching shape wins. A number is
# not taken as Aadhaar when a hyphen touches it, so hyphenated ids (UUIDs) stay readable.
_PERSONAL_DATA: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aadhaar", re.compile(r"(?<![\d-])\d{4} ?\d{4} ?\d{4}(?![\d-])")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("phone", re.compile(r"(?<!\d)(?:\+91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)")),
)


def mask_personal_data_in(value: Any) -> Any:
    """The value with every Aadhaar-, PAN-, e-mail- and phone-shaped text replaced by ``[aadhaar]``, ``[pan]``..."""
    if isinstance(value, str):
        for label, pattern in _PERSONAL_DATA:
            value = pattern.sub(f"[{label}]", value)
        return value
    if isinstance(value, dict):
        return {key: mask_personal_data_in(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_personal_data_in(item) for item in value]
    return value


def mask_personal_data(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: no log line leaves the service with personal data in it (OBS-04, LLM-06)."""
    return {key: mask_personal_data_in(value) for key, value in event_dict.items()}


def add_build_stamp(stamp: BuildStamp) -> Processor:
    """A processor that puts ``service``, ``env``, ``service_version``, ``commit`` and ``config_hash`` on every
    event, in every thread and for library loggers too (a contextvar would not reach worker threads)."""
    fields = stamp.log_fields()

    def processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        for key, value in fields.items():
            event_dict.setdefault(key, value)
        return event_dict

    return processor


def configure_logging(settings: LoggingSettings, stamp: BuildStamp | None = None) -> None:
    """Send structlog and standard-library records to stdout as JSON (or readable lines when local)."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # correlation_id, job_item_id, ... bound at the edge (OBS-03)
        *([add_build_stamp(stamp)] if stamp is not None else []),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        mask_personal_data,  # last, so it also covers values added by the processors above
    ]
    final: list[Processor] = (
        [structlog.processors.format_exc_info, mask_personal_data, structlog.processors.JSONRenderer()]
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
