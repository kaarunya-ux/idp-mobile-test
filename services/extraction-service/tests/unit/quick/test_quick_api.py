"""POST /api/v1/extract on the extraction-sync process over HTTP: multipart in, camelCase out, problem details for
each failure, and "busy" at once when every synchronous slot is taken."""

import threading
from collections.abc import Sequence
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from extraction.core.config import Settings
from extraction.core.errors import UnauthenticatedError
from extraction.core.permissions import DOCUMENT_WRITE
from extraction.core.security import Principal
from extraction.quick.application.quick_extract import QuickExtractService
from extraction.quick.domain.exceptions import BusyError
from extraction.quick.domain.models import (
    ExtractedField,
    PipelineOutcome,
    QuickCaller,
    QuickResult,
    ReadBy,
    StagedFile,
    Table,
    UploadLimits,
)
from extraction.sync_main import create_sync_app
from support.template_fakes import Clock

ACCOUNT = UUID("0192f0aa-0000-7000-8000-000000000001")
TEST_ACCOUNT = UUID("0192f0aa-0000-7000-8000-00000000a001")
USER = Principal(
    actor_id="u1", tenant_id=ACCOUNT, permissions=frozenset({DOCUMENT_WRITE}), kind="user", plan="PERSONAL"
)
JPEG = b"\xff\xd8\xff\xe0 made up"
GOOD_TOKEN = "good"  # noqa: S105  # a stand-in token the fake verifier accepts, not a secret
RESULT = QuickResult(
    document_type="aadhaar",
    document_confidence=0.94,
    fields=[
        ExtractedField(key="name", label="Name", type="string", value="परीक्षण व्यक्ति", confidence=0.96),
        ExtractedField(
            key="items", label="Items", type="table", value=Table(headers=["A"], rows=[["1"]]), confidence=0.8
        ),
    ],
    read_by=ReadBy.AI,
    model_id="vl-test",
    prompt_version="generic-read.v1",
)


class _Workspace:
    def open(self, caller: QuickCaller) -> str:
        return "acct/quick/1"

    def discard(self, folder: str) -> None:
        return None


class _Pipeline:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.callers: list[QuickCaller] = []

    def run(self, caller: QuickCaller, contents: Sequence[bytes], folder: str) -> PipelineOutcome:
        self.callers.append(caller)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        return PipelineOutcome(result=None, pages=[StagedFile(bucket="idp-derived", key="acct/quick/1/page-0.png")])

    def stage(self, contents: Sequence[bytes], folder: str) -> Sequence[StagedFile]:
        self.staged = len(contents)
        return [StagedFile(bucket="idp-derived", key="acct/quick/1/page-0.png")]


class _Reader:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def read(self, caller: QuickCaller, pages: Sequence[StagedFile]) -> QuickResult:
        if self.error is not None:
            raise self.error
        return RESULT


class _Verifier:
    """Accepts ``GOOD_TOKEN`` as ``principal``; anything else is not signed in."""

    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    async def verify(self, token: str) -> Principal:
        if token != GOOD_TOKEN or self.principal is None:
            raise UnauthenticatedError
        return self.principal


def _client(
    principal: Principal | None,
    *,
    pipeline: _Pipeline | None = None,
    reader: _Reader | None = None,
    max_file: int = 1000,
    max_concurrent: int = 2,
    tokenless: bool = False,
) -> TestClient:
    service = QuickExtractService(
        workspace=_Workspace(),
        pipeline=pipeline or _Pipeline(),
        reader=reader or _Reader(),
        limits=UploadLimits(max_file_bytes=max_file, max_total_bytes=1500),
    )
    quick = {"max_concurrent": max_concurrent} | (
        {"require_token": False, "default_account_id": str(TEST_ACCOUNT)} if tokenless else {}
    )
    app = create_sync_app(Clock(), Settings(_env_file=None, quick=quick), service)
    app.state.token_verifier = _Verifier(principal)
    return TestClient(app, headers={"Authorization": f"Bearer {GOOD_TOKEN}"} if principal is not None else {})


def test_returns_the_type_and_every_field_in_camel_case_when_a_front_and_back_are_sent() -> None:
    pipeline = _Pipeline()

    response = _client(USER, pipeline=pipeline).post(
        "/api/v1/extract", files={"file": ("f.jpg", JPEG), "back": ("b.jpg", JPEG)}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["documentType"], body["readBy"], body["promptVersion"]) == ("aadhaar", "AI", "generic-read.v1")
    assert body["fields"][0] == {
        "key": "name",
        "label": "Name",
        "type": "string",
        "value": "परीक्षण व्यक्ति",
        "confidence": 0.96,
        "status": None,
        "source": None,
    }
    assert body["fields"][1]["value"] == {"headers": ["A"], "rows": [["1"]]}
    assert pipeline.callers == [QuickCaller(account_id=ACCOUNT, plan="PERSONAL")]


def test_answers_401_without_a_token() -> None:
    assert _client(None).post("/api/v1/extract", files={"file": ("f.jpg", JPEG)}).json()["code"] == "EXT-401-AUTH-001"


def test_answers_403_when_the_token_has_no_account() -> None:
    response = _client(USER._replace(tenant_id=None)).post("/api/v1/extract", files={"file": ("f.jpg", JPEG)})

    assert (response.status_code, response.json()["code"]) == (403, "EXT-403-AUTH-001")


def test_answers_400_when_no_file_is_sent() -> None:
    assert _client(USER).post("/api/v1/extract", data={"x": "y"}).status_code == 400


def test_answers_413_when_the_file_is_over_the_limit() -> None:
    response = _client(USER, max_file=10).post("/api/v1/extract", files={"file": ("f.jpg", JPEG + b"x" * 50)})

    assert (response.status_code, response.json()["code"]) == (413, "EXT-413-FILE-001")


def test_answers_415_when_the_file_is_not_a_pdf_jpg_or_png() -> None:
    response = _client(USER).post("/api/v1/extract", files={"file": ("f.gif", b"GIF89a made up")})

    assert (response.status_code, response.json()["code"]) == (415, "EXT-415-FILE-001")


def test_answers_429_busy_when_ai_gateway_had_no_model_slot() -> None:
    response = _client(USER, reader=_Reader(BusyError())).post("/api/v1/extract", files={"file": ("f.jpg", JPEG)})

    assert (response.status_code, response.json()["code"]) == (429, "EXT-429-BUSY-001")


def test_answers_429_busy_at_once_when_every_synchronous_slot_is_taken() -> None:
    gate = threading.Event()
    client = _client(USER, pipeline=_Pipeline(gate), max_concurrent=1)
    first: list[int] = []
    running = threading.Thread(
        target=lambda: first.append(client.post("/api/v1/extract", files={"file": ("f.jpg", JPEG)}).status_code)
    )
    running.start()
    try:
        for _ in range(100):  # wait until the first request holds the only slot
            if client.app.state.quick_admission._free.locked():  # type: ignore[attr-defined]  # Starlette's app
                break
            threading.Event().wait(0.01)
        second = client.post("/api/v1/extract", files={"file": ("f.jpg", JPEG)})
    finally:
        gate.set()
        running.join(timeout=5)

    assert (second.status_code, second.json()["code"]) == (429, "EXT-429-BUSY-001")
    assert first == [200]


def test_reads_the_document_straight_with_the_ai_on_the_generic_route() -> None:
    pipeline = _Pipeline()

    response = _client(USER, pipeline=pipeline).post("/api/v1/extract/generic", files={"file": ("f.jpg", JPEG)})

    assert response.status_code == 200
    assert (response.json()["readBy"], response.json()["documentType"]) == ("AI", "aadhaar")
    assert (pipeline.callers, pipeline.staged) == ([], 1)  # no OCR run, one file staged


def test_answers_429_on_the_generic_route_when_ai_gateway_is_busy() -> None:
    response = _client(USER, reader=_Reader(BusyError())).post(
        "/api/v1/extract/generic", files={"file": ("f.jpg", JPEG)}
    )

    assert (response.status_code, response.json()["code"]) == (429, "EXT-429-BUSY-001")


def test_checks_the_files_on_the_generic_route_too() -> None:
    response = _client(USER).post("/api/v1/extract/generic", files={"file": ("f.gif", b"GIF89a made up")})

    assert (response.status_code, response.json()["code"]) == (415, "EXT-415-FILE-001")


def test_counts_a_tokenless_request_on_the_test_account_when_tokens_are_not_required() -> None:
    pipeline = _Pipeline()

    response = _client(None, pipeline=pipeline, tokenless=True).post("/api/v1/extract", files={"file": ("f.jpg", JPEG)})

    assert response.status_code == 200
    assert pipeline.callers == [QuickCaller(account_id=TEST_ACCOUNT, plan="CORPORATE")]


def test_still_checks_a_token_that_is_sent_when_tokens_are_not_required() -> None:
    client = _client(None, tokenless=True)

    response = client.post(
        "/api/v1/extract", files={"file": ("f.jpg", JPEG)}, headers={"Authorization": "Bearer forged"}
    )

    assert response.status_code == 401


def test_refuses_a_token_without_the_document_write_permission() -> None:
    reader_only = USER._replace(permissions=frozenset({"template:read"}))

    response = _client(reader_only).post("/api/v1/extract", files={"file": ("f.jpg", JPEG)})

    assert (response.status_code, response.json()["code"]) == (403, "EXT-403-AUTH-001")


def test_refuses_to_start_without_a_test_account_when_tokens_are_not_required() -> None:
    with pytest.raises(ValidationError, match="DEFAULT_ACCOUNT_ID"):
        Settings(_env_file=None, quick={"require_token": False})
