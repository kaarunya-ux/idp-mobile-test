"""Logging writes one JSON object per line and carries the job ids bound at the edge (OBS-01 to OBS-03)."""

import json

import pytest
import structlog

from idox_conversion.core.config import LoggingSettings
from idox_conversion.core.logging import bind_job, clear_job, configure_logging


def test_event_is_one_json_line_with_the_bound_ids(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))
    bind_job("job-1", "corr-1")

    structlog.get_logger("test").info("conversion_started", route="PDF_DOCX", pages=2)
    clear_job()

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "conversion_started"
    assert line["route"] == "PDF_DOCX"
    assert line["job_id"] == "job-1"
    assert line["correlation_id"] == "corr-1"


def test_ids_are_gone_after_clear_job(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))
    bind_job("job-1", "corr-1")
    clear_job()

    structlog.get_logger("test").info("later_event")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "job_id" not in line
