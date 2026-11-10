"""The OCR-first page reader: OCR reads the text, ai-gateway's page-layout lays the numbered lines out, and when either
can't help the page still comes back. A fake OCR engine and made-up page content only."""

import base64
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from PIL import Image

from conversion.conversion.domain.document_model import Alignment, Page, Table, TextBlock, TextKind
from conversion.conversion.domain.ports import StepContext
from conversion.conversion.infrastructure.readers.ocr_layout_reader import OcrLayoutPageReader
from idp_ocr_engines.errors import ModelFileError
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, OcrWord, PageImage

ACCOUNT = UUID("0192f0aa-0000-7000-8000-00000000a001")
CONTEXT = StepContext(account_id=ACCOUNT, key_prefix=f"{ACCOUNT}/job-1/item-1")
FALLBACK_PAGE = Page(blocks=(TextBlock(text="read by the page model"),))


def _word(text: str, y: float, line: int, block: int, x: float = 0.1) -> OcrWord:
    return OcrWord(text=text, box=Box(x=x, y=y, width=0.3, height=0.02), confidence=0.99, line_id=line, block_id=block)


WORDS = (
    _word("TEST LETTERHEAD", 0.05, 0, 0),
    _word("A made-up first line", 0.15, 1, 1),
    _word("and its second line.", 0.18, 2, 1),
    _word("Item", 0.30, 3, 2),
    _word("Amount", 0.30, 3, 2, x=0.5),
)
LAYOUT: dict[str, Any] = {
    "blocks": [
        {"kind": "heading", "lines": [1], "align": "center", "size": "large", "bold": True},
        {"kind": "paragraph", "lines": [2, 3], "align": "left", "size": "normal", "bold": False},
        {"kind": "table", "hasHeader": True, "rows": [[[4], [5]]]},
    ],
    "model": "page-model",
    "promptVersion": "page-layout@1",
    "latencyMs": 900,
    "tokens": {"input": 900, "output": 60},
}


class FakeOcr:
    def __init__(self, words: Sequence[OcrWord] = WORDS, error: Exception | None = None) -> None:
        self.words, self.error, self.pages = tuple(words), error, 0

    @property
    def engine_id(self) -> str:
        return "fake-ocr"

    @property
    def engine_version(self) -> str:
        return "1"

    def warm_up(self) -> None:
        return None

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        if self.error is not None:
            raise self.error
        self.pages += 1
        assert tuple(languages) == ("en",)
        return OcrPage(width=image.width, height=image.height, words=self.words)

    def read_region(self, image: PageImage, box: Box, languages: Sequence[str]) -> OcrRegion:
        raise NotImplementedError


class Tokens:
    def token(self) -> str:
        return "service-token"

    def invalidate(self) -> None:
        return None


class FallbackReader:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        self.pages.append(number)
        return FALLBACK_PAGE


def _reader(
    answer: httpx.Response, ocr: FakeOcr | None = None, *, sends_page: bool = True
) -> tuple[OcrLayoutPageReader, list[httpx.Request], FallbackReader, list[int]]:
    sent: list[httpx.Request] = []
    loads: list[int] = []
    engine = ocr or FakeOcr()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer

    def load() -> FakeOcr:
        loads.append(1)
        return engine

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(handler))
    fallback = FallbackReader()
    return OcrLayoutPageReader(load, client, Tokens(), fallback=fallback, sends_page=sends_page), sent, fallback, loads


def _picture(tmp_path: Path) -> Path:
    picture = tmp_path / "scan-page-0001.png"
    Image.new("RGB", (40, 50), "white").save(picture)
    return picture


def test_lays_out_ocrs_text_by_the_line_numbers_page_layout_answers(tmp_path: Path) -> None:
    reader, sent, fallback, _ = _reader(httpx.Response(200, json=LAYOUT))
    picture = _picture(tmp_path)

    page = reader.read_page(picture, 1, CONTEXT)

    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/internal/v1/ai/page-layout"
    assert sent[0].headers["Authorization"] == "Bearer service-token"
    assert body["accountId"] == str(ACCOUNT)
    assert body["page"] == {"data": base64.b64encode(picture.read_bytes()).decode()}
    assert body["lines"][0] == {"n": 1, "text": "TEST LETTERHEAD", "box": [0.1, 0.05, 0.3, 0.02]}
    assert [line["n"] for line in body["lines"]] == [1, 2, 3, 4, 5]
    assert page.blocks == (
        TextBlock(text="TEST LETTERHEAD", kind=TextKind.HEADING, level=1, alignment=Alignment.CENTER, bold=True),
        TextBlock(text="A made-up first line and its second line."),
        Table(rows=(("Item", "Amount"),), header_rows=1),
    )
    assert fallback.pages == []


def test_sends_only_the_lines_when_the_page_picture_is_switched_off(tmp_path: Path) -> None:
    reader, sent, _, _ = _reader(httpx.Response(200, json=LAYOUT), sends_page=False)

    page = reader.read_page(_picture(tmp_path), 1, CONTEXT)

    body = json.loads(sent[0].content)
    assert "page" not in body
    assert len(body["lines"]) == 5
    assert len(page.blocks) == 3


def test_loads_the_ocr_models_once_when_reading_several_pages(tmp_path: Path) -> None:
    reader, _, _, loads = _reader(httpx.Response(200, json=LAYOUT))
    picture = _picture(tmp_path)

    reader.read_page(picture, 1, CONTEXT)
    reader.read_page(picture, 2, CONTEXT)

    assert loads == [1]


@pytest.mark.parametrize("status", [429, 422, 503])
def test_keeps_ocrs_text_as_paragraphs_when_page_layout_cannot_answer(tmp_path: Path, status: int) -> None:
    reader, _, fallback, _ = _reader(httpx.Response(status, json={"code": "AI-503-MODEL-001"}))

    page = reader.read_page(_picture(tmp_path), 1, CONTEXT)

    assert [block.text for block in page.blocks if isinstance(block, TextBlock)] == [
        "TEST LETTERHEAD",
        "A made-up first line and its second line.",
        "Item Amount",
    ]
    assert fallback.pages == []


def test_keeps_ocrs_text_as_paragraphs_when_the_layout_answer_does_not_fit(tmp_path: Path) -> None:
    reader, _, _, _ = _reader(httpx.Response(200, json={"blocks": [{"kind": "poem", "lines": [1]}]}))

    page = reader.read_page(_picture(tmp_path), 1, CONTEXT)

    assert len(page.blocks) == 3


def test_reads_the_page_with_the_page_model_when_ocr_cannot_run(tmp_path: Path) -> None:
    reader, sent, fallback, _ = _reader(httpx.Response(200, json=LAYOUT), FakeOcr(error=ModelFileError("missing")))

    page = reader.read_page(_picture(tmp_path), 3, CONTEXT)

    assert page == FALLBACK_PAGE
    assert fallback.pages == [3]
    assert sent == []


def test_answers_an_empty_page_without_calling_the_model_when_ocr_finds_no_text(tmp_path: Path) -> None:
    reader, sent, _, _ = _reader(httpx.Response(200, json=LAYOUT), FakeOcr(words=()))

    page = reader.read_page(_picture(tmp_path), 1, CONTEXT)

    assert page.blocks == ()
    assert sent == []
