"""JSON log lines and the correlation id bound at the edge (OBS-01 to OBS-03)."""

import json
import re
from collections.abc import AsyncIterator

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from extraction.core.config import LoggingSettings
from extraction.core.logging import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    configure_logging,
    get_correlation_id,
)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/echo")
    async def get_echo() -> dict[str, str | None]:
        return {"seen": get_correlation_id()}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def test_writes_one_json_object_per_event_when_format_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))

    structlog.get_logger("test").info("page_read", pages=2)

    line = json.loads(capsys.readouterr().out.strip())
    assert (line["event"], line["pages"], line["level"]) == ("page_read", 2, "info")
    assert line["timestamp"].endswith("Z")


async def test_keeps_the_callers_correlation_id_when_it_is_valid(client: AsyncClient) -> None:
    response = await client.get("/echo", headers={CORRELATION_ID_HEADER: "01J8ZQ7M4T"})

    assert response.headers[CORRELATION_ID_HEADER] == "01J8ZQ7M4T"
    assert response.json() == {"seen": "01J8ZQ7M4T"}


@pytest.mark.parametrize("bad_value", ["has spaces", "semi;colon", "x" * 65])
async def test_replaces_the_correlation_id_when_it_is_malformed(client: AsyncClient, bad_value: str) -> None:
    response = await client.get("/echo", headers={CORRELATION_ID_HEADER: bad_value})

    assert re.fullmatch(r"[0-9a-f-]{36}", response.headers[CORRELATION_ID_HEADER])


async def test_makes_a_correlation_id_when_none_is_sent(client: AsyncClient) -> None:
    response = await client.get("/echo")

    assert response.json()["seen"] == response.headers[CORRELATION_ID_HEADER]


def test_has_no_correlation_id_when_called_outside_a_request() -> None:
    structlog.contextvars.clear_contextvars()

    assert get_correlation_id() is None


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
