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


def test_masks_personal_data_when_a_value_reaches_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").warning(
        "cell_rejected",
        detail="aadhaar 1234 5678 9012, pan ABCDE1234F, mail test.user@example.com, phone +91 98765 43210",
        job_id="0192f0aa-0000-7000-8000-00000000a001",
    )

    record = json.loads(_last_line(capsys))
    assert record["detail"] == "aadhaar [aadhaar], pan [pan], mail [email], phone [phone]"
    assert record["job_id"] == "0192f0aa-0000-7000-8000-00000000a001"  # ids are not personal data


def test_masks_personal_data_inside_nested_values_when_a_list_or_dict_is_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").info("rows_skipped", rows=[{"id": "ABCDE1234F"}, ("9876543210",)])

    assert json.loads(_last_line(capsys))["rows"] == [{"id": "[pan]"}, ["[phone]"]]


def test_masks_personal_data_in_a_traceback_when_an_exception_message_quotes_a_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The traceback is text made after the other processors; a library error that quotes a cell must not leak it."""
    configure_logging(LoggingSettings(level="INFO", format="json"))

    try:
        raise ValueError("cell 1234 5678 9012 is not a date")
    except ValueError:
        structlog.get_logger("test").exception("step_failed", job_id="j-1")

    exception = json.loads(_last_line(capsys))["exception"]
    assert "1234 5678 9012" not in exception
    assert "[aadhaar]" in exception
