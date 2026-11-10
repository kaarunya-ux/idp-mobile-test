"""A scanned page rebuilt from what OCR read and how the page model laid it out (ADR 0001,
``AI_GATEWAY__PAGE_READER=ocr_layout``).

OCR reads every piece of text on the page (a line, or one cell of a table) and numbers it. The page model answers
only how those numbers are organised: which pieces make a heading, a paragraph, a list, or the cells of a table. The
text in the document is always OCR's, never the model's, so the model can't misspell or invent any of it. A piece the
model left out is kept, as a paragraph where it sits on the page. Without the model (ai-gateway down, or the daily cap
reached) the page is OCR's own blocks as paragraphs: plainer, but every word is there.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import groupby

from conversion.conversion.domain.document_model import Alignment, Block, Page, Table, TextBlock, TextKind


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrLine:
    """One piece of text OCR read, in reading order. ``top`` is its top edge as a fraction of the page height;
    ``block`` is OCR's own grouping (lines that sit close together, such as an address)."""

    number: int
    text: str
    top: float
    block: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LaidOutText:
    """Text the model found: its role and look, and the numbers of its lines in reading order."""

    kind: TextKind
    lines: tuple[int, ...]
    level: int = 1
    alignment: Alignment = Alignment.LEFT
    bold: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LaidOutTable:
    """A table the model found: each cell is the numbers of the lines in it."""

    rows: tuple[tuple[tuple[int, ...], ...], ...]
    header_rows: int = 0


type Layout = LaidOutText | LaidOutTable


def laid_out_page(lines: Sequence[OcrLine], layout: Iterable[Layout]) -> Page:
    """The page as the model laid it out, with OCR's text. A line is used once (the first block naming it wins); a
    number OCR never gave is ignored; a line no block names becomes a paragraph where it sits."""
    by_number = {line.number: line for line in lines}
    used: set[int] = set()

    def take(numbers: Iterable[int]) -> list[OcrLine]:
        found = [by_number[n] for n in dict.fromkeys(numbers) if n in by_number and n not in used]
        used.update(line.number for line in found)
        return found

    placed: list[tuple[float, Block]] = []
    for item in layout:
        placed.extend(_text(item, take) if isinstance(item, LaidOutText) else _table(item, take))
    for top, paragraph in _paragraphs([line for line in lines if line.number not in used]):
        at = next((index for index, (other, _) in enumerate(placed) if other > top), len(placed))
        placed.insert(at, (top, paragraph))
    return Page(blocks=tuple(block for _, block in placed))


def ocr_page(lines: Sequence[OcrLine]) -> Page:
    """The page without the model: each of OCR's blocks is a paragraph."""
    return Page(blocks=tuple(paragraph for _, paragraph in _paragraphs(lines)))


def _text(item: LaidOutText, take: Callable[[Iterable[int]], list[OcrLine]]) -> list[tuple[float, Block]]:
    found = take(item.lines)
    if not found:
        return []
    if item.kind is TextKind.LIST_ITEM:  # one item per line, as the model is asked to give them
        return [
            (line.top, TextBlock(text=line.text, kind=item.kind, alignment=item.alignment, bold=item.bold))
            for line in found
        ]
    block = TextBlock(text=_joined(found), kind=item.kind, level=item.level, alignment=item.alignment, bold=item.bold)
    return [(min(line.top for line in found), block)]


def _table(item: LaidOutTable, take: Callable[[Iterable[int]], list[OcrLine]]) -> list[tuple[float, Block]]:
    cells = [[take(cell) for cell in row] for row in item.rows]
    tops = [line.top for row in cells for cell in row for line in cell]
    if not tops:
        return []
    kept = [row for row in cells if any(row)]  # a row whose every line went elsewhere is no row
    header_rows = item.header_rows if cells[0] and any(cells[0]) else 0
    table = Table.from_ragged(
        [[_joined(cell) for cell in row] for row in kept], header_rows=min(header_rows, len(kept))
    )
    return [(min(tops), table)]


def _paragraphs(lines: Sequence[OcrLine]) -> list[tuple[float, TextBlock]]:
    """OCR's consecutive lines of one block, as one paragraph each, with the top of its first line."""
    groups = [list(group) for _, group in groupby(lines, key=lambda line: line.block)]
    return [(group[0].top, TextBlock(text=_joined(group))) for group in groups]


def _joined(lines: Sequence[OcrLine]) -> str:
    return " ".join(line.text for line in lines)
