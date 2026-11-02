"""The model-first page reader: the picture goes to storage, ai-gateway's page-read answers, the answer becomes the
document model. Made-up page content only."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from conversion.conversion.domain.document_model import Alignment, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import AiLimitReachedError, AiUnavailableError, UnreadableImageError
from conversion.conversion.domain.ports import StepContext, UploadedObject
from conversion.conversion.domain.values import ObjectRef
from conversion.conversion.infrastructure.readers.model_reader import GatewayPageReader, to_page

ACCOUNT = UUID("0192f0aa-0000-7000-8000-00000000a001")
CONTEXT = StepContext(account_id=ACCOUNT, key_prefix=f"{ACCOUNT}/job-1/item-1")


class Storage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str]] = []

    def upload(self, source: Path, ref: ObjectRef, content_type: str) -> UploadedObject:
        self.uploads.append((ref.bucket, ref.key, content_type))
        return UploadedObject(size=source.stat().st_size, sha256="0" * 64)

    def download(self, ref: ObjectRef, destination: Path) -> None:
        raise AssertionError("not used")


class Tokens:
    def __init__(self) -> None:
        self.invalidated = 0

    def token(self) -> str:
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


ANSWER: dict[str, Any] = {
    "analysis": {"nBlocks": 4, "tableColumnCounts": [3]},
    "blocks": [
        {"kind": "heading", "text": "TEST LETTERHEAD", "align": "center", "size": "large", "bold": True},
        {"kind": "paragraph", "text": "A made-up paragraph.", "align": "left", "size": "normal", "bold": False},
        {"kind": "list", "text": "• first point\n• second point", "align": "left", "size": "normal", "bold": False},
        {
            "kind": "table",
            "hasHeader": True,
            "nCols": 3,
            "header": ["Item", "Qty", "Amount"],
            "rows": [["Test A", "2"], ["Test B", "1", "12.50", "extra"]],
        },
    ],
}


def _reader(answer: httpx.Response, tokens: Tokens | None = None) -> tuple[GatewayPageReader, Storage, list]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(handler))
    storage = Storage()
    return GatewayPageReader(client, tokens or Tokens(), storage, bucket="idp-derived"), storage, sent


def _picture(tmp_path: Path) -> Path:
    picture = tmp_path / "scan-page-0001.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(16))
    return picture


def test_uploads_the_page_then_reads_it_through_the_gateway(tmp_path: Path) -> None:
    reader, storage, sent = _reader(httpx.Response(200, json=ANSWER))

    page = reader.read_page(_picture(tmp_path), 1, CONTEXT)

    assert storage.uploads == [("idp-derived", f"{ACCOUNT}/job-1/item-1/model-pages/page-0001.png", "image/png")]
    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/internal/v1/ai/page-read"
    assert sent[0].headers["Authorization"] == "Bearer service-token"
    assert body == {"accountId": str(ACCOUNT), "page": {"bucket": "idp-derived", "key": storage.uploads[0][1]}}
    assert len(page.blocks) == 5


def test_keeps_each_blocks_role_and_look_and_squares_up_tables() -> None:
    heading, paragraph, first, second, table = to_page(ANSWER["blocks"]).blocks

    assert heading == TextBlock(
        text="TEST LETTERHEAD", kind=TextKind.HEADING, level=1, alignment=Alignment.CENTER, bold=True
    )
    assert paragraph == TextBlock(text="A made-up paragraph.")
    assert (first, second) == (
        TextBlock(text="first point", kind=TextKind.LIST_ITEM),
        TextBlock(text="second point", kind=TextKind.LIST_ITEM),
    )
    assert table == Table(
        rows=(("Item", "Qty", "Amount"), ("Test A", "2", ""), ("Test B", "1", "12.50")), header_rows=1
    )


def test_leaves_out_picture_text_empty_blocks_and_empty_tables() -> None:
    page = to_page(
        [
            {"kind": "image", "text": "a logo that says TEST"},
            {"kind": "paragraph", "text": "   "},
            {"kind": "table", "hasHeader": False, "nCols": 2, "header": [], "rows": [["", ""]]},
            {"kind": "heading", "text": "Section", "size": "normal"},
        ]
    )

    assert page.blocks == (TextBlock(text="Section", kind=TextKind.HEADING, level=2),)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429, json={"code": "AIG-429-CAP-001"}), AiLimitReachedError),
        (httpx.Response(422, json={"code": "AIG-422-INPUT-001"}), UnreadableImageError),
        (httpx.Response(503, json={"code": "AIG-503-BUSY-001"}), AiUnavailableError),
        (httpx.Response(200, text="not json"), AiUnavailableError),
        (httpx.Response(200, json={"analysis": {}}), AiUnavailableError),
    ],
)
def test_turns_gateway_refusals_into_conversion_errors(tmp_path: Path, response: httpx.Response, error: type) -> None:
    reader, _, _ = _reader(response)

    with pytest.raises(error):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)


def test_drops_the_token_when_the_gateway_refuses_it(tmp_path: Path) -> None:
    tokens = Tokens()
    reader, _, _ = _reader(httpx.Response(401), tokens)

    with pytest.raises(AiUnavailableError):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)
    assert tokens.invalidated == 1


def test_reports_ai_unavailable_when_the_gateway_cannot_be_reached(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(refuse))
    reader = GatewayPageReader(client, Tokens(), Storage(), bucket="idp-derived")

    with pytest.raises(AiUnavailableError):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)
