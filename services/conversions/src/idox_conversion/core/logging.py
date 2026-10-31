"""Structured logging to stdout through structlog, with ids bound at the edge of every job (OBS-01 to OBS-03).

Log an event name plus key-values, never an f-string::

    log.info("conversion_started", route="PDF_DOCX", pages=page_count)

Never log document content, extracted text or prompts (OBS-04, LLM-06): ids, sizes, token counts, latency and outcome.
"""

import logging
import sys

import structlog
from structlog.types import Processor

from idox_conversion.core.config import LoggingSettings

JOB_ID_KEY = "job_id"
CORRELATION_ID_KEY = "correlation_id"


def configure_logging(settings: LoggingSettings) -> None:
    """Send structlog and standard-library records to stdout as JSON (or readable lines when local)."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # job_id, correlation_id, ... bound at the edge (OBS-03)
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


def bind_job(job_id: str, correlation_id: str) -> None:
    """Attach the job and correlation ids to every log line of this task, until ``clear_job`` (OBS-03)."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**{JOB_ID_KEY: job_id, CORRELATION_ID_KEY: correlation_id})


def clear_job() -> None:
    """Drop the ids bound by ``bind_job``."""
    structlog.contextvars.clear_contextvars()
