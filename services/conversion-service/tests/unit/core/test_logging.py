"""Logs are one JSON object per line on stdout, including records from other libraries (OBS-01, OBS-02)."""

import json
import logging

import pytest
import structlog

from conversion.core.config import LoggingSettings
from conversion.core.logging import configure_logging


def _last_line(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_writes_event_and_fields_as_json_when_format_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").info("conversion_completed", job_id="j-1", pages=3)

    record = json.loads(_last_line(capsys))
    assert record["event"] == "conversion_completed"
    assert record["job_id"] == "j-1"
    assert record["pages"] == 3
    assert record["level"] == "info"
    assert record["timestamp"].endswith("Z")


def test_formats_standard_library_records_the_same_way_when_a_library_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    logging.getLogger("uvicorn.error").warning("server_restarting")

    record = json.loads(_last_line(capsys))
    assert record["event"] == "server_restarting"
    assert record["logger"] == "uvicorn.error"


def test_drops_debug_records_when_level_is_info(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").debug("noisy_detail")

    assert "noisy_detail" not in capsys.readouterr().out


def test_writes_readable_lines_when_format_is_console(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="console"))

    structlog.get_logger("test").info("conversion_completed", job_id="j-1")

    line = _last_line(capsys)
    assert "conversion_completed" in line
    assert not line.startswith("{")
