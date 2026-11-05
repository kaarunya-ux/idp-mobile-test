"""``PageReaderPort`` OCR-first (ADR 0001, ``AI_GATEWAY__PAGE_READER=ocr_layout``): PP-OCR reads the page's text on
this machine, and ai-gateway's ``page-layout`` says how the numbered lines are laid out (IDP-03 ch. 4: only the
gateway talks to the model server). The model writes a few line numbers instead of the whole page, so a page takes
seconds, and the text is OCR's exactly (``domain.page_layout``).

When OCR can't run (its models missing, say), the page goes to the model-first reader as before. When the model
can't answer (ai-gateway down, the daily cap reached, an answer that doesn't fit), the page keeps OCR's text as plain
paragraphs rather than failing the job.
"""

import base64
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import structlog
from PIL import Image

from conversion.conversion.domain.document_model import Alignment, Page, TextKind
from conversion.conversion.domain.exceptions import AiLimitReachedError, AiUnavailableError, UnreadableImageError
from conversion.conversion.domain.page_layout import LaidOutTable, LaidOutText, Layout, OcrLine, laid_out_page, ocr_page
from conversion.conversion.domain.ports import PageReaderPort, StepContext
from conversion.conversion.infrastructure.readers.model_reader import post
from conversion.core.service_auth import ServiceTokenProvider
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.model import OcrWord
from idp_ocr_engines.port import OcrEnginePort

log = structlog.get_logger(__name__)

PAGE_LAYOUT = "/internal/v1/ai/page-layout"
LANGUAGES = ("en",)  # English only, as the sync lane
MAX_LINES = 2000  # ai-gateway takes at most this many lines in one call
_MAX_TEXT = 500  # ... each at most this long: the model needs a line's words to place it, not all of them
_ALIGNMENT = {"left": Alignment.LEFT, "center": Alignment.CENTER, "right": Alignment.RIGHT}
_TEXT_KINDS = {
    "heading": TextKind.HEADING,
    "paragraph": TextKind.PARAGRAPH,
    "list": TextKind.LIST_ITEM,
    "caption": TextKind.CAPTION,
}


class OcrLayoutPageReader:
    def __init__(
        self,
        ocr: Callable[[], OcrEnginePort],
        client: httpx.Client,
        tokens: ServiceTokenProvider,
        *,
        fallback: PageReaderPort,
    ) -> None:
        self._load_ocr = ocr  # loaded on the first page: only the worker that reads scans holds the models
        self._ocr: OcrEnginePort | None = None
        self._loading = threading.Lock()
        self._client = client  # base URL and timeouts are set by bootstrap
        self._tokens = tokens
        self._fallback = fallback

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        try:
            words = self._words(image)
        except OcrEngineError as exc:
            log.warning("page_ocr_failed", page=number, error=type(exc).__name__)
            return self._fallback.read_page(image, number, context)
        lines = [_line(n, word) for n, word in enumerate(words, 1)]
        if not lines:  # nothing printed on it
            return Page(blocks=())
        if len(lines) > MAX_LINES:
            log.info("page_layout_skipped", page=number, lines=len(lines))
            return ocr_page(lines)
        try:
            layout = self._layout(image, words, context)
        except (AiUnavailableError, AiLimitReachedError, UnreadableImageError) as exc:
            log.warning("page_layout_unavailable", page=number, error=type(exc).__name__)
            return ocr_page(lines)
        return laid_out_page(lines, layout)

    def _words(self, image: Path) -> Sequence[OcrWord]:
        with self._loading:
            if self._ocr is None:
                self._ocr = self._load_ocr()
        with Image.open(image) as picture:
            page = from_rgb_array(np.asarray(picture.convert("RGB")))
        return [word for word in self._ocr.read_page(page, LANGUAGES).words if word.text.strip()]

    def _layout(self, image: Path, words: Sequence[OcrWord], context: StepContext) -> list[Layout]:
        body = {
            "accountId": str(context.account_id),
            "page": {"data": base64.b64encode(image.read_bytes()).decode()},
            "lines": [
                {
                    "n": n,
                    "text": word.text.strip()[:_MAX_TEXT],
                    "box": [round(v, 4) for v in (word.box.x, word.box.y, word.box.width, word.box.height)],
                }
                for n, word in enumerate(words, 1)
            ],
        }
        answer = post(self._client, self._tokens, PAGE_LAYOUT, body)
        try:
            return [_laid_out(block) for block in answer["blocks"]]
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("page_layout_answer_unreadable")
            raise AiUnavailableError from exc


def _line(number: int, word: OcrWord) -> OcrLine:
    return OcrLine(number=number, text=word.text.strip(), top=word.box.y, block=word.block_id)


def _laid_out(block: dict[str, Any]) -> Layout:
    if block["kind"] == "table":
        rows = tuple(tuple(tuple(int(n) for n in cell) for cell in row) for row in block["rows"])
        return LaidOutTable(rows=rows, header_rows=1 if block.get("hasHeader") else 0)
    kind = _TEXT_KINDS[block["kind"]]
    large = block.get("size") == "large"
    return LaidOutText(
        kind=kind,
        lines=tuple(int(n) for n in block["lines"]),
        level=2 if kind is TextKind.HEADING and not large else 1,
        alignment=_ALIGNMENT.get(str(block.get("align")), Alignment.LEFT),
        bold=bool(block.get("bold")),
    )
