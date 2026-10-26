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
