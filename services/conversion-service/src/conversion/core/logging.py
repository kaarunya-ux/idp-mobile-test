"""Structured logging to stdout through structlog (standard OBS-01, OBS-02).

Log an event name plus key-values, never an f-string::

    log.info("conversion_completed", job_id=str(job_id), pages=page_count)

Standard-library and Uvicorn loggers are routed through the same renderer, so every line has one shape.
"""

import logging
import sys

import structlog
from structlog.types import Processor

from conversion.core.config import LoggingSettings

_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(settings: LoggingSettings) -> None:
    """Send structlog and standard-library records to stdout as JSON (or readable lines when local)."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # correlation_id, job_id, ... bound at the edge (OBS-03)
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
