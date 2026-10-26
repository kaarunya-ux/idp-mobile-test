"""Digital PDF -> document model with pdfplumber (IDP-03 ch. 3.4: ``pdf_tables``, and PDF -> DOCX).

IDP-03 names pdf2docx for PDF -> DOCX, but it is built on PyMuPDF (AGPL), which the licensing decision rules out.
So digital PDFs take the same path scanned ones do: read into the model, then ``model_to_docx`` or a sheet writer.

What is read, page by page, from the PDF's own text layer (no OCR, no AI):

- **Tables** with ruled lines (pdfplumber's "lines" strategy). A box with fewer than two rows or two columns is
  a frame around text, not a table, so its text is read as text.
- **Text** outside the tables, as lines merged into paragraphs when they sit close together in the same style.
  A line clearly larger than the document's body text is a heading; the largest size is level 1. A line starting
  with a bullet or a number is a list item, one level deeper per ``INDENT_STEP_PT`` of extra indent.

The PDF is opened with PDFium first, so a password-protected or damaged file gets the same error as in
``pdf_to_images``. Scanned pages have no text layer; they take the OCR route instead (planner variant SCANNED).
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pdfplumber
from pdfplumber.page import Page as PdfPage
from pdfplumber.utils.exceptions import PdfminerException

from conversion.conversion.domain.cell_values import has_header
from conversion.conversion.domain.document_model import (
    MAX_HEADING_LEVEL,
    Alignment,
    Block,
    DocumentModel,
    Page,
    Table,
    TextBlock,
    TextKind,
)
from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf
from conversion.conversion.infrastructure.model_file import model_path, write_model

HEADING_SIZE_RATIO = 1.15  # a line this much larger than the body text is a heading
INDENT_STEP_PT = 18  # a quarter inch more indent than the page's first-level bullets is one level deeper
_PARAGRAPH_GAP_RATIO = 0.8  # a gap above a line wider than this share of the line's height starts a paragraph
_SAME_SIZE_PT = 0.5
_CENTRE_TOLERANCE = 0.05  # share of the page width a centred line's middle may be off the page's middle
_FULL_WIDTH = 0.7  # a line wider than this share of the page reads as running text, not centred
_EDGE_TOLERANCE_PT = 2.0
_MIN_TABLE_SIDE = 2
# Bullet glyphs, en dash and hyphen; Word and LibreOffice draw default bullets with the Symbol and Wingdings
# fonts, whose characters land in Unicode's private area (U+F0B7 is Symbol's bullet).
_BULLET = re.compile("^[\u2022\u25cf\u25aa\u25e6\u2023\u2043\u2013\uf0b7\uf0a7\uf076\uf0d8\uf0fc*-]\\s+")
_NUMBERED = re.compile(r"^(\d{1,3}|[a-zA-Z])[.)]\s+")
_BOLD_FONT = re.compile(r"bold|black|heavy", re.IGNORECASE)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Line:
    text: str
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    bold: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class _PageContent:
    width: float
    lines: list[_Line]
    tables: list[tuple[float, Table]]  # (top, table)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Layout:
    """What a page's lines are measured against."""

    body_size: float
    heading_levels: dict[float, int]  # heading size -> level
    page_width: float
    right: float  # the page's right text margin
    list_left: float  # where the page's least indented list items start


class PdfplumberReader:
    """Writes ``<input>.model.json``: one unnamed page per selected PDF page."""

    def __init__(self, *, max_pages: int) -> None:
        self._max_pages = max_pages

    @property
    def converter(self) -> Converter:
        return Converter.PDF_TO_MODEL

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs
        with open_pdf(source) as document:  # PDFium: a clear error for a password or a damaged file
            page_count = len(document)
        if page_count > self._max_pages:
            raise LimitExceededError(f"The PDF has more than {self._max_pages:,} pages.")
        options = request.options
        indexes = options.page_range.select(page_count) if options.page_range else range(page_count)
        try:
            with pdfplumber.open(source) as pdf:
                contents = [_read_page(pdf.pages[index]) for index in indexes]
        except PdfminerException as exc:  # PDFium repaired something pdfminer can't parse
            raise CorruptPdfError from exc
        body_size, heading_levels = _text_styles([line for content in contents for line in content.lines])
        pages = tuple(Page(blocks=_blocks(content, body_size, heading_levels)) for content in contents)
        output = model_path(request.output_dir, source)
        write_model(DocumentModel(pages=pages), output)
        return StepResult(outputs=(output,), page_count=len(pages))


def _read_page(page: PdfPage) -> _PageContent:
    try:
        tables: list[tuple[float, Table]] = []
        text_area = page
        for found in page.find_tables():
            rows = [[" ".join((cell or "").split()) for cell in row] for row in found.extract()]
            if len(rows) < _MIN_TABLE_SIDE or len(rows[0]) < _MIN_TABLE_SIDE or not any(any(row) for row in rows):
                continue  # a frame around text: its text is read below
            tables.append((found.bbox[1], Table.from_ragged(rows, header_rows=1 if has_header(rows) else 0)))
            text_area = text_area.outside_bbox(found.bbox, strict=False)
        lines = [_line(raw) for raw in text_area.extract_text_lines(return_chars=True, strip=True) if raw["text"]]
        return _PageContent(width=float(page.width), lines=lines, tables=tables)
    finally:
        page.close()  # drop the page's parsed objects; a long PDF is read one page at a time


def _line(raw: Mapping[str, Any]) -> _Line:
    """One of pdfplumber's text lines, as plain typed values."""
    chars = raw["chars"]
    sizes = Counter(round(float(char["size"]), 1) for char in chars)
    return _Line(
        text=str(raw["text"]),
        top=float(raw["top"]),
        bottom=float(raw["bottom"]),
        x0=float(raw["x0"]),
        x1=float(raw["x1"]),
        size=sizes.most_common(1)[0][0],
        bold=all(_BOLD_FONT.search(str(char["fontname"])) for char in chars if str(char["text"]).strip()),
    )


def _text_styles(lines: Sequence[_Line]) -> tuple[float, dict[float, int]]:
    """The body text size (the size most characters have) and a level for each larger, heading size."""
    weights = Counter[float]()
    for line in lines:
        weights[line.size] += len(line.text)
    body_size = weights.most_common(1)[0][0] if weights else 0.0
    heading_sizes = sorted({line.size for line in lines if line.size >= body_size * HEADING_SIZE_RATIO}, reverse=True)
    return body_size, {size: min(level, MAX_HEADING_LEVEL) for level, size in enumerate(heading_sizes, start=1)}


def _blocks(content: _PageContent, body_size: float, heading_levels: dict[float, int]) -> tuple[Block, ...]:
    """Paragraphs and tables in reading order (top to bottom)."""
    layout = _Layout(
        body_size=body_size,
        heading_levels=heading_levels,
        page_width=content.width,
        right=max((line.x1 for line in content.lines), default=content.width),
        list_left=min((line.x0 for line in content.lines if _is_list_start(line.text)), default=0.0),
    )
    placed: list[tuple[float, Block]] = list(content.tables)
    paragraph: list[_Line] = []
    for line in content.lines:
        if paragraph and _continues(paragraph[-1], line):
            paragraph.append(line)
            continue
        if paragraph:
            placed.append((paragraph[0].top, _text_block(paragraph, layout)))
        paragraph = [line]
    if paragraph:
        placed.append((paragraph[0].top, _text_block(paragraph, layout)))
    return tuple(block for _, block in sorted(placed, key=lambda item: item[0]))


def _continues(previous: _Line, line: _Line) -> bool:
    """Whether ``line`` carries on the paragraph that ``previous`` ends."""
    gap = line.top - previous.bottom
    return (
        0 <= gap <= (previous.bottom - previous.top) * _PARAGRAPH_GAP_RATIO
        and abs(line.size - previous.size) <= _SAME_SIZE_PT
        and line.bold == previous.bold
        and not _is_list_start(line.text)
    )


def _text_block(lines: list[_Line], layout: _Layout) -> TextBlock:
    first = lines[0]
    text = " ".join(line.text for line in lines)
    if first.size in layout.heading_levels:
        kind, level = TextKind.HEADING, layout.heading_levels[first.size]
    elif _is_list_start(first.text):
        depth = 1 + round((first.x0 - layout.list_left) / INDENT_STEP_PT)
        kind, level = TextKind.LIST_ITEM, min(depth, MAX_HEADING_LEVEL)
        text = _BULLET.sub("", text) or text  # the bullet glyph goes; Word draws its own. Numbers stay
    else:
        kind, level = TextKind.PARAGRAPH, 1
    return TextBlock(text=text, kind=kind, level=level, alignment=_alignment(lines, layout), bold=first.bold)


def _is_list_start(text: str) -> bool:
    return bool(_BULLET.match(text) or _NUMBERED.match(text))


def _alignment(lines: list[_Line], layout: _Layout) -> Alignment:
    """Centred when every line sits in the middle of the page; right-aligned when every line ends at the margin."""
    middle = layout.page_width / 2

    def centred(line: _Line) -> bool:
        narrow = line.x1 - line.x0 < layout.page_width * _FULL_WIDTH
        return narrow and abs((line.x0 + line.x1) / 2 - middle) <= layout.page_width * _CENTRE_TOLERANCE

    def right_aligned(line: _Line) -> bool:
        return line.x1 >= layout.right - _EDGE_TOLERANCE_PT and line.x0 > middle

    if all(centred(line) for line in lines):
        return Alignment.CENTER
    if all(right_aligned(line) for line in lines):
        return Alignment.RIGHT
    return Alignment.LEFT
