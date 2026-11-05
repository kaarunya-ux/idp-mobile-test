"""The model-first page reader: the picture goes inside ai-gateway's page-read call, its answer becomes the document
model. Made-up page content only."""

import base64
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from conversion.conversion.domain.document_model import Alignment, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import AiLimitReachedError, AiUnavailableError, UnreadableImageError
from conversion.conversion.domain.ports import StepContext
from conversion.conversion.infrastructure.readers.model_reader import GatewayPageReader, to_page

ACCOUNT = UUID("0192f0aa-0000-7000-8000-00000000a001")
CONTEXT = StepContext(account_id=ACCOUNT, key_prefix=f"{ACCOUNT}/job-1/item-1")


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


def _reader(answer: httpx.Response, tokens: Tokens | None = None) -> tuple[GatewayPageReader, list]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(handler))
    return GatewayPageReader(client, tokens or Tokens()), sent


def _picture(tmp_path: Path) -> Path:
    picture = tmp_path / "scan-page-0001.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(16))
    return picture


def test_sends_the_page_picture_inside_the_call_and_reads_the_answer(tmp_path: Path) -> None:
    reader, sent = _reader(httpx.Response(200, json=ANSWER))
    picture = _picture(tmp_path)

    page = reader.read_page(picture, 1, CONTEXT)

    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/internal/v1/ai/page-read"
    assert sent[0].headers["Authorization"] == "Bearer service-token"
    assert body == {"accountId": str(ACCOUNT), "page": {"data": base64.b64encode(picture.read_bytes()).decode()}}
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
    reader, _ = _reader(response)

    with pytest.raises(error):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)


def test_drops_the_token_when_the_gateway_refuses_it(tmp_path: Path) -> None:
    tokens = Tokens()
    reader, _ = _reader(httpx.Response(401), tokens)

    with pytest.raises(AiUnavailableError):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)
    assert tokens.invalidated == 1


def test_reports_ai_unavailable_when_the_gateway_cannot_be_reached(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(refuse))
    reader = GatewayPageReader(client, Tokens())

    with pytest.raises(AiUnavailableError):
        reader.read_page(_picture(tmp_path), 1, CONTEXT)
