"""POST /api/v1/convert: the upload reaches the use case, the answer is the file, every error is problem details."""

import asyncio
import json
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conversion.conversion.application.services.convert_now import (
    ConvertNowCommand,
    ConvertNowResult,
    MergeNowCommand,
)
from conversion.conversion.domain.exceptions import NeedsReadingError
from conversion.core.config import Settings
from conversion.core.errors import DomainError, UnauthenticatedError
from conversion.core.permissions import DOCUMENT_WRITE
from conversion.core.security import Principal
from conversion.sync_main import create_sync_app

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE: dict[str, Any] = {"file": ("report.pdf", b"%PDF-made-up", "application/pdf")}
BEARER = {"Authorization": "Bearer made-up"}


class FakeRunner:
    def __init__(self, error: DomainError | None = None) -> None:
        self.commands: list[ConvertNowCommand | MergeNowCommand] = []
        self.uploads: list[bytes] = []
        self.error = error
        self.release: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def merge(self, command: MergeNowCommand) -> ConvertNowResult:
        return await self.convert(command)

    async def convert(self, command: ConvertNowCommand | MergeNowCommand) -> ConvertNowResult:
        self.commands.append(command)
        self.uploads.extend(upload.path.read_bytes() for upload in command.uploads)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        output = command.folder / "input.docx"
        output.write_bytes(b"the converted file")
        return ConvertNowResult(file=output, content_type=DOCX, page_count=3)


class FakeVerifier:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    async def verify(self, token: str) -> Principal:
        if self.principal is None:
            raise UnauthenticatedError
        return self.principal


def _principal(*permissions: str) -> Principal:
    return Principal(actor_id="user-1", tenant_id=None, permissions=frozenset(permissions), kind="user")


def _app(runner: FakeRunner, **sync: object) -> FastAPI:
    settings = Settings.model_validate({"sync": {"require_token": False, "max_concurrent": 1, **sync}})
    return create_sync_app(settings=settings, runner=runner)


def _http(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
async def client(runner: FakeRunner) -> AsyncIterator[AsyncClient]:
    async with _http(_app(runner)) as http:
        yield http


async def test_answers_with_the_converted_file_when_the_conversion_succeeds(
    client: AsyncClient, runner: FakeRunner
) -> None:
    response = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"})

    assert response.status_code == HTTPStatus.OK
    assert response.content == b"the converted file"
    assert response.headers["content-type"] == DOCX
    assert response.headers["x-page-count"] == "3"
    assert 'filename="report.docx"' in response.headers["content-disposition"]
    assert "x-correlation-id" in response.headers
    assert (runner.uploads[0], runner.commands[0].target, runner.commands[0].options) == (b"%PDF-made-up", "DOCX", {})


async def test_passes_the_options_on_when_they_are_a_json_object(client: AsyncClient, runner: FakeRunner) -> None:
    options = json.dumps({"pageRange": "1-2", "dpi": 150})

    await client.post("/api/v1/convert", files=FILE, data={"target": "PNG", "options": options})

    assert runner.commands[0].options == {"pageRange": "1-2", "dpi": 150}


@pytest.mark.parametrize("options", ["not json", "[1, 2]"])
async def test_answers_422_when_the_options_are_not_a_json_object(
    client: AsyncClient, runner: FakeRunner, options: str
) -> None:
    response = await client.post("/api/v1/convert", files=FILE, data={"target": "PNG", "options": options})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "CNV-422-OPTIONS-001"
    assert runner.commands == []


async def test_never_uses_the_callers_file_name_as_a_path_and_makes_it_safe_for_the_header(
    client: AsyncClient, runner: FakeRunner
) -> None:
    files = {"file": ("..\\..\\etc\\pass wd .pdf", b"%PDF-made-up", "application/pdf")}

    response = await client.post("/api/v1/convert", files=files, data={"target": "DOCX"})

    assert runner.commands[0].uploads[0].path.name == "upload"
    assert 'filename="pass_wd.docx"' in response.headers["content-disposition"]


async def test_removes_the_working_folder_when_the_answer_has_gone(client: AsyncClient, runner: FakeRunner) -> None:
    await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"})

    assert not runner.commands[0].folder.exists()


async def test_removes_the_working_folder_and_answers_the_errors_code_when_the_conversion_fails() -> None:
    runner = FakeRunner(error=NeedsReadingError())
    async with _http(_app(runner)) as client:
        response = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "CNV-422-SYNC-001"
    assert not runner.commands[0].folder.exists()


async def test_passes_every_file_in_order_and_names_the_answer_generically_when_several_are_sent(
    client: AsyncClient, runner: FakeRunner
) -> None:
    files = [
        ("file", ("a.pdf", b"%PDF-first", "application/pdf")),
        ("file", ("b.pdf", b"%PDF-second", "application/pdf")),
    ]

    response = await client.post("/api/v1/convert", files=files, data={"target": "DOCX"})

    assert runner.uploads == [b"%PDF-first", b"%PDF-second"]
    assert [upload.name for upload in runner.commands[0].uploads] == ["a", "b"]
    assert 'filename="converted.docx"' in response.headers["content-disposition"]


async def test_merges_the_files_in_the_order_sent_without_a_target(client: AsyncClient, runner: FakeRunner) -> None:
    files = [("file", ("a.pdf", b"%PDF-first", "application/pdf")), ("file", ("b.png", b"\x89PNG-second", "image/png"))]

    response = await client.post("/api/v1/merge", files=files)

    assert response.status_code == HTTPStatus.OK
    assert isinstance(runner.commands[0], MergeNowCommand)
    assert runner.uploads == [b"%PDF-first", b"\x89PNG-second"]
    assert 'filename="merged.docx"' in response.headers["content-disposition"]  # the fake's output


async def test_answers_413_when_there_are_more_files_or_bytes_than_one_request_may_carry(runner: FakeRunner) -> None:
    two = [("file", ("a.pdf", b"%PDF-1234", "application/pdf")), ("file", ("b.pdf", b"%PDF-5678", "application/pdf"))]
    async with _http(_app(runner, max_files=1)) as client:
        too_many = await client.post("/api/v1/convert", files=two, data={"target": "DOCX"})
    async with _http(_app(runner, max_total_size=12)) as client:
        too_big = await client.post("/api/v1/merge", files=two)

    assert [too_many.status_code, too_big.status_code] == [HTTPStatus.REQUEST_ENTITY_TOO_LARGE] * 2
    assert runner.commands == []


async def test_answers_413_without_converting_when_the_file_is_over_the_limit(runner: FakeRunner) -> None:
    files = {"file": ("big.pdf", b"%PDF" + bytes(2048), "application/pdf")}
    async with _http(_app(runner, max_file_size=1024)) as client:
        response = await client.post("/api/v1/convert", files=files, data={"target": "DOCX"})

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response.json()["code"] == "CNV-413-FILE-001"
    assert runner.commands == []


async def test_answers_422_when_the_file_is_empty(client: AsyncClient, runner: FakeRunner) -> None:
    files = {"file": ("empty.pdf", b"", "application/pdf")}

    response = await client.post("/api/v1/convert", files=files, data={"target": "DOCX"})

    assert response.json()["code"] == "CNV-422-FILE-001"
    assert runner.commands == []


async def test_answers_400_with_the_field_when_the_target_is_missing(client: AsyncClient) -> None:
    response = await client.post("/api/v1/convert", files=FILE)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert body["code"] == "CNV-400-REQUEST-001"
    assert body["errors"][0]["field"] == "target"


async def test_answers_429_at_once_when_every_slot_is_taken(client: AsyncClient, runner: FakeRunner) -> None:
    runner.release = asyncio.Event()
    first = asyncio.create_task(client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"}))
    await runner.entered.wait()

    second = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"})
    runner.release.set()

    assert second.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert second.json()["code"] == "CNV-429-BUSY-001"
    assert (await first).status_code == HTTPStatus.OK


async def test_answers_401_when_a_token_is_required_and_none_is_sent(runner: FakeRunner) -> None:
    async with _http(_app(runner, require_token=True)) as client:
        response = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "CNV-401-AUTH-001"
    assert runner.commands == []


async def test_converts_when_a_required_token_has_the_permission(runner: FakeRunner) -> None:
    app = _app(runner, require_token=True)
    app.state.token_verifier = FakeVerifier(_principal(DOCUMENT_WRITE))
    async with _http(app) as client:
        response = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"}, headers=BEARER)

    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    ("principal", "status", "code"),
    [
        (None, HTTPStatus.UNAUTHORIZED, "CNV-401-AUTH-001"),  # a bad token is refused even when none is required
        (_principal(), HTTPStatus.FORBIDDEN, "CNV-403-AUTH-001"),
    ],
)
async def test_refuses_a_token_that_is_bad_or_has_no_permission(
    runner: FakeRunner, principal: Principal | None, status: HTTPStatus, code: str
) -> None:
    app = _app(runner)
    app.state.token_verifier = FakeVerifier(principal)
    async with _http(app) as client:
        response = await client.post("/api/v1/convert", files=FILE, data={"target": "DOCX"}, headers=BEARER)

    assert (response.status_code, response.json()["code"]) == (status, code)
    assert runner.commands == []


async def test_serves_the_probes_when_asked(client: AsyncClient) -> None:
    assert (await client.get("/health/ready")).json()["status"] == "UP"
    assert (await client.get("/health/live")).status_code == HTTPStatus.OK
