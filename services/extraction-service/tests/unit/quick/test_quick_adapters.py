"""The synchronous extract's adapters: ai-gateway's generic-read (its codes told apart), the workspace in MinIO, and
the pipeline runner (the pipeline's result mapped, long PDFs refused, the AI on the INTERACTIVE lane)."""

import base64
import io
import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from PIL import Image

from extraction.pipeline.domain.exceptions import TooManyPagesError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.infrastructure.ai.inline_images import BUCKET, InlineImages
from extraction.quick.domain.exceptions import (
    AiLimitReachedError,
    AiUnavailableError,
    BusyError,
    FileUnreadableError,
)
from extraction.quick.domain.models import QuickCaller, ReadBy, StagedFile, Table
from extraction.quick.infrastructure.gateway_reader import GatewayDocumentReader
from extraction.quick.infrastructure.pipeline_runner import PipelineDocumentRunner, quick_result
from extraction.quick.infrastructure.workspace import MemoryWorkspace
from idp_ocr_engines.model import PageImage
from support.pipeline_fakes import RESULT, Pipeline

CALLER = QuickCaller(account_id=UUID("0192f0aa-0000-7000-8000-000000000001"), plan="GUEST")
PAGES = [StagedFile(bucket="idp-derived", key="acct/quick/1/page-0.png")]
ANSWER = {
    "documentType": "invoice",
    "documentConfidence": 0.9,
    "fields": [
        {"key": "invoice_number", "label": "Invoice No", "type": "string", "value": "INV-1", "confidence": 0.9},
        {
            "key": "items",
            "label": "Items",
            "type": "table",
            "value": {"headers": ["A"], "rows": [["1"]]},
            "confidence": 0.8,
        },
        {"key": "signature", "label": "Signature", "type": "string", "value": None, "confidence": None},
    ],
    "model": "vl-test",
    "promptVersion": "generic-read.v1",
}


class FakeTokens:
    def __init__(self) -> None:
        self.invalidated = 0

    def token(self) -> str:
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


def _reader(
    status: int, body: dict[str, Any], seen: list[httpx.Request] | None = None
) -> tuple[GatewayDocumentReader, FakeTokens]:
    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(answer))
    tokens = FakeTokens()
    return GatewayDocumentReader(client, tokens, InlineImages()), tokens  # type: ignore[arg-type]  # a stand-in token provider


def test_sends_the_pages_on_the_interactive_lane_with_the_plan_and_returns_every_field() -> None:
    seen: list[httpx.Request] = []
    reader, _ = _reader(200, ANSWER, seen)

    result = reader.read(CALLER, PAGES)

    body = json.loads(seen[0].content)
    assert (body["priority"], body["plan"]) == ("INTERACTIVE", "GUEST")
    assert body["pages"] == [{"bucket": "idp-derived", "key": "acct/quick/1/page-0.png"}]
    assert seen[0].headers["authorization"] == "Bearer service-token"
    assert (result.document_type, result.read_by) == ("invoice", ReadBy.AI)
    assert isinstance(result.fields[1].value, Table)
    assert (result.fields[2].value, result.fields[2].confidence) == (None, None)


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (503, "AIG-503-BUSY-001", BusyError),
        (429, "AIG-429-CAP-001", AiLimitReachedError),
        (422, "AIG-422-INPUT-001", FileUnreadableError),
        (503, "AIG-503-MODEL-001", AiUnavailableError),
        (502, "AIG-502-OUTPUT-001", AiUnavailableError),
    ],
)
def test_tells_the_gateways_failures_apart(status: int, code: str, error: type[Exception]) -> None:
    reader, _ = _reader(status, {"code": code})

    with pytest.raises(error):
        reader.read(CALLER, PAGES)


def test_drops_the_token_when_the_gateway_refuses_it() -> None:
    reader, tokens = _reader(401, {"code": "AIG-401-AUTH-001"})

    with pytest.raises(AiUnavailableError):
        reader.read(CALLER, PAGES)
    assert tokens.invalidated == 1


def test_answers_unavailable_when_the_gateway_answer_is_not_the_shape() -> None:
    reader, _ = _reader(200, {"fields": "nope"})

    with pytest.raises(AiUnavailableError):
        reader.read(CALLER, PAGES)


def test_answers_unavailable_when_the_gateway_cannot_be_reached() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(fail))
    with pytest.raises(AiUnavailableError):
        GatewayDocumentReader(client, FakeTokens(), InlineImages()).read(CALLER, PAGES)  # type: ignore[arg-type]  # a stand-in token provider


def test_drops_only_the_requests_own_images_when_the_workspace_is_discarded() -> None:
    images = InlineImages()
    workspace = MemoryWorkspace(images)
    folder, other = workspace.open(CALLER), workspace.open(CALLER)
    page = PageImage(width=2, height=2, rgb=bytes(12))
    images.save_page(ObjectRef(bucket=BUCKET, key=f"{folder}/page-0.png"), page)
    images.save_page(ObjectRef(bucket=BUCKET, key=f"{folder}/crops/name.png"), page)
    images.save_page(ObjectRef(bucket=BUCKET, key=f"{other}/page-0.png"), page)

    workspace.discard(folder)

    assert folder.startswith(f"{CALLER.account_id}/quick/")
    assert len(images) == 1
    assert images.get(ObjectRef(bucket=BUCKET, key=f"{other}/page-0.png")) is not None


def test_sends_a_page_held_in_memory_inside_the_request() -> None:
    seen: list[httpx.Request] = []
    images = InlineImages()
    page = StagedFile(bucket=BUCKET, key="acct/quick/1/page-0.png")
    images.save_page(ObjectRef(bucket=page.bucket, key=page.key), PageImage(width=2, height=2, rgb=bytes(12)))

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(answer))
    GatewayDocumentReader(client, FakeTokens(), images).read(CALLER, [page])  # type: ignore[arg-type]  # a stand-in token provider

    sent = json.loads(seen[0].content)["pages"][0]
    assert set(sent) == {"data"}  # the image itself, no storage key
    assert base64.b64decode(sent["data"])[:3] == b"\xff\xd8\xff"  # a JPEG


def _template_result() -> dict[str, Any]:
    fields = [
        {
            "key": "pan_number",
            "label": "PAN",
            "type": "ID",
            "value": "ABCPE1234F",
            "confidence": 0.95,
            "status": "ACCEPTED",
            "source": "OCR_AGREE",
        },
        {
            "key": "date_of_birth",
            "label": "Date of Birth",
            "type": "DATE",
            "value": "01/01/1990",
            "confidence": 0.9,
            "status": "ACCEPTED",
            "source": "AI",
        },
        {
            "key": "name",
            "label": "Name",
            "type": "NAME",
            "value": None,
            "confidence": 0.0,
            "status": "NEEDS_REVIEW",
            "source": None,
        },
    ]
    return RESULT | {"fields": fields}


def test_maps_a_template_result_with_who_read_each_field() -> None:
    found = quick_result(_template_result())

    assert found is not None
    assert (found.document_type, found.document_confidence, found.read_by) == ("pan_card", 0.97, ReadBy.OCR_AND_AI)
    assert [(f.key, f.type, f.source) for f in found.fields] == [
        ("pan_number", "string", "OCR_AGREE"),
        ("date_of_birth", "date", "AI"),
        ("name", "string", None),
    ]
    assert found.fields[2].confidence is None  # no value, no confidence


def test_says_ocr_alone_read_it_when_no_model_was_asked() -> None:
    found = quick_result(_template_result() | {"provenance": {"models": []}})

    assert found is not None
    assert (found.read_by, found.model_id) == (ReadBy.OCR, None)


def test_maps_no_template_to_none() -> None:
    assert quick_result(RESULT | {"template": None}) is None


class FakePages:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save_page(self, ref: Any, page: Any) -> None:
        self.saved.append(ref.key)


class FakeLoader:
    """Three page images per file, whatever it is: the runner keeps the first two."""

    def load_pages(self, content: bytes) -> tuple[PageImage, ...]:
        return tuple(PageImage(width=2, height=2, rgb=bytes(12), page_index=i) for i in range(3))


def _pdf(pages: int) -> bytes:
    buffer = io.BytesIO()
    sheets = [Image.new("RGB", (60, 40), "gray") for _ in range(pages)]
    sheets[0].save(buffer, format="PDF", save_all=True, append_images=sheets[1:])
    return buffer.getvalue()


def test_runs_the_pipeline_with_the_crops_folder_on_the_interactive_lane() -> None:
    pipeline, pages = _Recording(), FakePages()
    runner = PipelineDocumentRunner(pipeline, FakeLoader(), pages, bucket="idp-derived", max_pages=2)  # type: ignore[arg-type]  # a stand-in pipeline

    outcome = runner.run(CALLER, [b"\xff\xd8\xff made up", b"\xff\xd8\xff made up"], "acct/quick/1")

    context = pipeline.command.ai_context
    assert (context.interactive, context.plan) == (True, "GUEST")
    assert context.crop_folder.key == "acct/quick/1/crops"
    assert pages.saved == ["acct/quick/1/page-0.png", "acct/quick/1/page-1.png"]
    assert [p.page_index for p in pipeline.command.pages] == [0, 1]  # the back continues the numbering
    assert outcome.result is not None
    assert [p.key for p in outcome.pages] == pages.saved


def test_refuses_a_pdf_longer_than_the_synchronous_limit_before_any_work() -> None:
    pipeline = _Recording()
    runner = PipelineDocumentRunner(pipeline, FakeLoader(), FakePages(), bucket="idp-derived", max_pages=2)  # type: ignore[arg-type]  # a stand-in pipeline

    with pytest.raises(TooManyPagesError):
        runner.run(CALLER, [_pdf(3)], "acct/quick/1")
    assert pipeline.command is None


class _Recording(Pipeline):
    """The shared stand-in pipeline, keeping the command the extract step got."""

    def __init__(self) -> None:
        super().__init__()
        self.command: Any = None

    def extract_service(self, progress: Any, templates: Any) -> Any:
        inner = super().extract_service(progress, templates)
        outer = self

        class _Keep:
            def extract(self, command: Any) -> Any:
                outer.command = command
                inner.extract(command)
                return _template_result()  # with every field's value, as the real pipeline's

        return _Keep()


def test_stores_the_first_two_page_images_without_ocr_for_the_generic_route() -> None:
    pipeline, pages = _Recording(), FakePages()
    runner = PipelineDocumentRunner(pipeline, FakeLoader(), pages, bucket="idp-derived", max_pages=2)  # type: ignore[arg-type]  # a stand-in pipeline

    staged = runner.stage([b"\xff\xd8\xff made up"], "acct/quick/1")

    assert [p.key for p in staged] == ["acct/quick/1/page-0.png", "acct/quick/1/page-1.png"]
    assert pipeline.command is None  # no OCR, no extract step


def test_refuses_a_long_pdf_on_the_generic_route_too() -> None:
    runner = PipelineDocumentRunner(_Recording(), FakeLoader(), FakePages(), bucket="idp-derived", max_pages=2)  # type: ignore[arg-type]  # a stand-in pipeline

    with pytest.raises(TooManyPagesError):
        runner.stage([_pdf(3)], "acct/quick/1")
