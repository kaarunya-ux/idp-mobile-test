"""Every error leaves as RFC 9457 problem details, in the same shape as the Java services (ERR-04, ERR-06)."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict, Field

from extraction.core.error_codes import ErrorCode
from extraction.core.errors import DomainError
from extraction.core.logging import CORRELATION_ID_HEADER
from extraction.core.problem_details import PROBLEM_MEDIA_TYPE
from extraction.main import create_app


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 21, 10, 14, 22, 987654, tzinfo=UTC)


class WidgetBrokenError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.REQUEST_INVALID
    title: ClassVar[str] = "Widget is broken"


class WidgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=10)
    lines: list[int] = Field(max_length=2)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(clock=FixedClock())

    @app.get("/test/domain-error")
    async def get_domain_error() -> None:
        raise WidgetBrokenError("The widget cannot be used.")

    @app.get("/test/unexpected-error")
    async def get_unexpected_error() -> None:
        raise RuntimeError("password=hunter2 at /srv/secret/path.py")

    @app.post("/test/widgets")
    async def create_widget(request: WidgetRequest) -> None:
        return None

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_returns_problem_details_when_a_domain_error_is_raised(client: AsyncClient) -> None:
    response = await client.get("/test/domain-error", headers={CORRELATION_ID_HEADER: "01J8ZQ7M4T"})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Widget is broken",
        "status": 400,
        "code": "EXT-400-VALIDATION-001",
        "detail": "The widget cannot be used.",
        "correlationId": "01J8ZQ7M4T",
        "timestamp": "2026-10-21T10:14:22Z",
    }


async def test_lists_each_invalid_field_when_the_request_fails_validation(client: AsyncClient) -> None:
    response = await client.post("/test/widgets", json={"name": "ab", "lines": [1, 2, 3]})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert body["code"] == "EXT-400-VALIDATION-001"
    assert sorted(problem["field"] for problem in body["errors"]) == ["lines", "name"]


async def test_does_not_echo_the_submitted_value_when_validation_fails(client: AsyncClient) -> None:
    response = await client.post("/test/widgets", json={"name": "ABCDE1234F-too-long", "lines": []})

    assert "ABCDE1234F" not in response.text


async def test_names_the_nested_field_when_an_unknown_field_is_sent(client: AsyncClient) -> None:
    response = await client.post("/test/widgets", json={"name": "abc", "lines": [], "extra": 1})

    assert [problem["field"] for problem in response.json()["errors"]] == ["extra"]


async def test_hides_the_cause_when_an_unexpected_error_occurs(client: AsyncClient) -> None:
    response = await client.get("/test/unexpected-error")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["code"] == "EXT-500-INTERNAL-001"
    assert "hunter2" not in response.text
    assert "/srv/secret" not in response.text
    assert "Traceback" not in response.text


async def test_makes_a_correlation_id_when_the_caller_sent_none(client: AsyncClient) -> None:
    response = await client.get("/test/domain-error")

    assert re.fullmatch(r"[0-9a-f-]{36}", response.json()["correlationId"])
    assert response.json()["correlationId"] == response.headers[CORRELATION_ID_HEADER]
