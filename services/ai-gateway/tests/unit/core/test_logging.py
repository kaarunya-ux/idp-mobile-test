"""Logs are one JSON object per line, and no personal data leaves in one (OBS-01, OBS-04)."""

import json

import pytest
import structlog

from ai_gateway.core.config import LoggingSettings
from ai_gateway.core.logging import configure_logging


def _last_log_line(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_masks_personal_data_when_a_value_reaches_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").warning(
        "cell_rejected",
        detail="aadhaar 1234 5678 9012, pan ABCDE1234F, mail test.user@example.com, phone +91 98765 43210",
        job_id="0192f0aa-0000-7000-8000-00000000a001",
    )

    record = json.loads(_last_log_line(capsys))
    assert record["detail"] == "aadhaar [aadhaar], pan [pan], mail [email], phone [phone]"
    assert record["job_id"] == "0192f0aa-0000-7000-8000-00000000a001"  # ids are not personal data


def test_masks_personal_data_inside_nested_values_when_a_list_or_dict_is_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").info("rows_skipped", rows=[{"id": "ABCDE1234F"}, ("9876543210",)])

    assert json.loads(_last_log_line(capsys))["rows"] == [{"id": "[pan]"}, ["[phone]"]]


def test_masks_personal_data_in_a_traceback_when_an_exception_message_quotes_a_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The traceback is text made after the other processors; a library error that quotes a cell must not leak it."""
    configure_logging(LoggingSettings(level="INFO", format="json"))

    try:
        raise ValueError("cell 1234 5678 9012 is not a date")
    except ValueError:
        structlog.get_logger("test").exception("step_failed", job_id="j-1")

    exception = json.loads(_last_log_line(capsys))["exception"]
    assert "1234 5678 9012" not in exception
    assert "[aadhaar]" in exception
