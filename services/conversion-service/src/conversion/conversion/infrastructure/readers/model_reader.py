"""``PageReaderPort`` with a large vision model, through ai-gateway's ``page-read`` (IDP-03 ch. 4: only the gateway
talks to the model server).

The model-first reading is Sumukh's (``services/conversions``, PR #20): the model looks at the page picture and
writes its blocks (headings, paragraphs, lists, tables) with how each looks (centred, large, bold). This module only
moves the picture to storage, asks the gateway, and turns its answer into the document model; the prompt and the
answer's schema live in ai-gateway (``prompts/page-read.v1.md``).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import structlog

from conversion.conversion.domain.document_model import Alignment, Block, Page, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import AiLimitReachedError, AiUnavailableError, UnreadableImageError
from conversion.conversion.domain.ports import ObjectStoragePort, StepContext
from conversion.conversion.domain.values import ObjectRef
from conversion.core.service_auth import ServiceTokenError, ServiceTokenProvider

log = structlog.get_logger(__name__)

PAGE_READ = "/internal/v1/ai/page-read"
_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_ALIGNMENT = {"left": Alignment.LEFT, "center": Alignment.CENTER, "right": Alignment.RIGHT}
_TEXT_KINDS = {"heading": TextKind.HEADING, "paragraph": TextKind.PARAGRAPH, "caption": TextKind.CAPTION}
_BULLET = ("•", "-", "–", "*", "·")  # noqa: RUF001 - the en dash is a bullet the model copies from the page


class GatewayPageReader:
    def __init__(
        self, client: httpx.Client, tokens: ServiceTokenProvider, storage: ObjectStoragePort, *, bucket: str
    ) -> None:
        self._client = client  # base URL and timeouts are set by bootstrap
        self._tokens = tokens
        self._storage = storage
        self._bucket = bucket

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        ref = ObjectRef(bucket=self._bucket, key=f"{context.key_prefix}/model-pages/page-{number:04d}{image.suffix}")
        self._storage.upload(image, ref, _CONTENT_TYPES.get(image.suffix.lower(), "image/png"))
        answer = self._post({"accountId": str(context.account_id), "page": {"bucket": ref.bucket, "key": ref.key}})
        try:
            return to_page(answer["blocks"])
        except (KeyError, TypeError) as exc:
            log.warning("page_read_answer_unreadable", page=number)
            raise AiUnavailableError from exc

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(
                PAGE_READ, json=body, headers={"Authorization": f"Bearer {self._tokens.token()}"}
            )
        except (httpx.HTTPError, ServiceTokenError) as exc:  # the details go to the log, never to the user
            log.warning("ai_gateway_unreachable", error=type(exc).__name__)
            raise AiUnavailableError from exc
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()  # the next call fetches a new token
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise AiLimitReachedError
        if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            raise UnreadableImageError
        if response.status_code != httpx.codes.OK:
            log.warning("ai_gateway_error", status=response.status_code, code=_code(response))
            raise AiUnavailableError
        try:
            answer = response.json()
        except ValueError as exc:
            raise AiUnavailableError from exc
        if not isinstance(answer, dict):
            raise AiUnavailableError
        return answer


def to_page(blocks: list[dict[str, Any]]) -> Page:
    """The gateway's blocks as the document model's: text keeps its role and look, a list becomes list items, a
    table becomes a rectangular grid (short rows padded, long rows cut to the model's own column count). A picture's
    own text is left out: nothing on the page can confirm it."""
    return Page(blocks=tuple(block for item in blocks for block in _blocks(item)))


def _blocks(item: dict[str, Any]) -> Iterator[Block]:
    kind = item["kind"]
    if kind == "table":
        table = _table(item)
        if table is not None:
            yield table
        return
    text = str(item.get("text") or "").strip()
    if kind == "image" or not text:
        return
    alignment, bold = _ALIGNMENT.get(str(item.get("align")), Alignment.LEFT), bool(item.get("bold"))
    if kind == "list":
        for line in (raw.strip().lstrip("".join(_BULLET)).strip() for raw in text.splitlines()):
            if line:
                yield TextBlock(text=line, kind=TextKind.LIST_ITEM, alignment=alignment, bold=bold)
        return
    level = 1 if item.get("size") == "large" else 2
    yield TextBlock(
        text=text,
        kind=_TEXT_KINDS.get(kind, TextKind.PARAGRAPH),
        level=level if kind == "heading" else 1,
        alignment=alignment,
        bold=bold,
    )


def _table(item: dict[str, Any]) -> Table | None:
    header = [str(cell) for cell in item.get("header") or []]
    rows = [[str(cell) for cell in row] for row in item.get("rows") or []]
    has_header = bool(item.get("hasHeader")) and bool(header)
    grid = ([header] if has_header else []) + rows
    width = int(item.get("nCols") or 0) or max((len(row) for row in grid), default=0)
    if not grid or width == 0:
        return None
    fitted = [tuple((row + [""] * width)[:width]) for row in grid]
    if not any(cell.strip() for row in fitted for cell in row):
        return None
    return Table(rows=tuple(fitted), header_rows=1 if has_header else 0)


def _code(response: httpx.Response) -> str | None:
    """The problem-details code, for the log; never the detail text."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None
