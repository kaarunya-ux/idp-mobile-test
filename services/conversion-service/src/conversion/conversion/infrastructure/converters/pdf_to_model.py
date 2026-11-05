"""Digital PDF -> document model with pdfplumber (IDP-03 ch. 3.4: ``pdf_tables``, and PDF -> DOCX).

IDP-03 names pdf2docx for PDF -> DOCX, but it is built on PyMuPDF (AGPL), which the licensing decision rules out.
So digital PDFs take the same path scanned ones do: read into the model, then ``model_to_docx`` or a sheet writer.

What is read, page by page, from the PDF's own text layer (no OCR, no AI):

- **Tables** with ruled lines (pdfplumber's "lines" strategy). A box with fewer than two rows or two columns is
  a frame around text, not a table, so its text is read as text. Then tables drawn with only a rule under each
  row, as browsers print them (``pdf_ruled_tables``).
- **Text** outside the tables, as lines merged into paragraphs when they sit close together in the same style.
  Text a few points off its line (inline code, set lower) stays in the line. A line clearly larger than the
  document's body text is a heading; the largest size is level 1. A line starting with a bullet (a character, or a
  small drawn dot just left of it) or a number is a list item; the page's list indents, left to right, are its
  levels. A word broken across lines at a hyphen is joined back without a space.

The PDF is opened with PDFium first, so a password-protected or damaged file gets the same error as in
``pdf_to_images``. Scanned pages have no text layer; they take the OCR route instead (planner variant SCANNED).
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from conversion.conversion.infrastructure.converters.pdf_ruled_tables import RuledTable, reach, ruled_tables
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf
from conversion.conversion.infrastructure.model_file import model_path, write_model

HEADING_SIZE_RATIO = 1.15  # a line this much larger than the body text is a heading
_SAME_INDENT_PT = 4.0  # list items starting this close are at one level
# running headers and footers (a browser prints the file name, date and "1 / 8") sit in this share of the page at the
# top and the bottom; a line there that every other page or more repeats, numbers aside, is one and is left out
_RUNNING_MARGIN = 0.05
_PARAGRAPH_GAP_RATIO = 0.8  # a gap above a line wider than this share of the line's height starts a paragraph
_SAME_SIZE_PT = 0.5
_CENTRE_TOLERANCE = 0.05  # share of the page width a centred line's middle may be off the page's middle
_FULL_WIDTH = 0.7  # a line wider than this share of the page reads as running text, not centred
_EDGE_TOLERANCE_PT = 2.0
_MIN_TABLE_SIDE = 2
# characters this far apart vertically are on one line: a browser sets inline code 3.1 pt lower than the text around
# it, just past pdfplumber's default of 3, which cut every sentence with code in two. Lines are 12 pt or more apart
_SAME_LINE_PT = 4.0
_BULLET_MAX_PT = 6.0  # a drawn mark no larger than this ...
_BULLET_GAP_PT = 15.0  # ... ending at most this far left of a line's first character is that line's bullet
_JOINT_PT = 1.0
_BROKEN_WORD = re.compile(r"[^\W\d_]-$")  # a line ending in a letter and a hyphen: the word goes on below
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
    bullet: bool = False  # a drawn bullet sits just left of it
    indent: float = 0.0  # where its list marker starts: the drawn bullet, else its first character


@dataclass(frozen=True, slots=True, kw_only=True)
class _PageContent:
    width: float
    height: float
    lines: list[_Line]
    tables: list[tuple[float, Table]]  # (top, table)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Layout:
    """What a page's lines are measured against."""

    body_size: float
    heading_levels: dict[float, int]  # heading size -> level
    page_width: float
    right: float  # the page's right text margin
    list_indents: tuple[float, ...]  # where the page's list items start, one per level, left to right


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
                contents = _without_running_lines([_read_page(pdf.pages[index]) for index in indexes])
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
            table = _table(found.extract(), header_rows=None)
            if table is None:
                continue  # a frame around text: its text is read below
            tables.append((found.bbox[1], table))
            text_area = text_area.outside_bbox(found.bbox, strict=False)
        for bbox, table in _ruled(text_area):
            tables.append((bbox[1], table))
            text_area = text_area.outside_bbox(bbox, strict=False)
        bullets = _bullets(page)
        raw_lines = text_area.extract_text_lines(return_chars=True, strip=True, y_tolerance=_SAME_LINE_PT)
        lines = [_line(raw, bullets) for raw in raw_lines if raw["text"]]
        return _PageContent(width=float(page.width), height=float(page.height), lines=lines, tables=tables)
    finally:
        page.close()  # drop the page's parsed objects; a long PDF is read one page at a time


def _table(rows: Sequence[Sequence[str | None]], *, header_rows: int | None) -> Table | None:
    """A grid of at least two rows and two columns with some text; ``header_rows`` None: guessed from the cells."""
    cells = [[_joined((cell or "").split("\n")) for cell in row] for row in rows]
    if len(cells) < _MIN_TABLE_SIDE or len(cells[0]) < _MIN_TABLE_SIDE or not any(any(row) for row in cells):
        return None
    guessed = 1 if has_header(cells) else 0
    return Table.from_ragged(cells, header_rows=guessed if header_rows is None else header_rows)


def _ruled(area: PdfPage) -> list[tuple[tuple[float, float, float, float], Table]]:
    """Tables drawn with a rule under each row: the rows between the rules, the columns at the rules' joints."""
    found: list[tuple[tuple[float, float, float, float], Table]] = []
    rules = ruled_tables(area)
    if not rules:
        return found
    raw = area.extract_text_lines(strip=True, y_tolerance=_SAME_LINE_PT)
    for ruled in rules:
        top, bottom = reach(ruled, _lines_within(raw, ruled), _PARAGRAPH_GAP_RATIO)
        bbox = (ruled.columns[0], top - _JOINT_PT, ruled.columns[-1], bottom + _JOINT_PT)
        settings = {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": list(ruled.columns),
            "horizontal_strategy": "explicit",
            "explicit_horizontal_lines": sorted({bbox[1], *ruled.rules, bbox[3]}),
            "text_y_tolerance": _SAME_LINE_PT,
        }
        table = _table(area.crop(bbox).extract_table(settings) or [], header_rows=int(top < ruled.rules[0] - _JOINT_PT))
        if table is not None:
            found.append((bbox, table))
    return found


def _lines_within(raw: Sequence[Mapping[str, Any]], ruled: RuledTable) -> list[tuple[float, float]]:
    """The (top, bottom) of the text lines inside the table's columns."""
    left, right = ruled.columns[0] - _JOINT_PT, ruled.columns[-1] + _JOINT_PT
    return [(float(line["top"]), float(line["bottom"])) for line in raw if left <= line["x0"] and line["x1"] <= right]


def _bullets(page: PdfPage) -> list[tuple[float, float, float]]:
    """The left and right edges and middle height of each small drawn mark: a bullet a browser drew as a dot or a
    square."""
    marks: list[dict[str, Any]] = [*page.curves, *page.rects]
    return [
        (float(mark["x0"]), float(mark["x1"]), (float(mark["top"]) + float(mark["bottom"])) / 2)
        for mark in marks
        if float(mark["x1"]) - float(mark["x0"]) <= _BULLET_MAX_PT
        and float(mark["bottom"]) - float(mark["top"]) <= _BULLET_MAX_PT
    ]


def _line(raw: Mapping[str, Any], bullets: Sequence[tuple[float, float, float]] = ()) -> _Line:
    """One of pdfplumber's text lines, as plain typed values."""
    chars = raw["chars"]
    sizes = Counter(round(float(char["size"]), 1) for char in chars)
    top, bottom, x0 = float(raw["top"]), float(raw["bottom"]), float(raw["x0"])
    bullet = next(
        (left for left, right, middle in bullets if 0 <= x0 - right <= _BULLET_GAP_PT and top <= middle <= bottom), None
    )
    return _Line(
        text=str(raw["text"]),
        top=top,
        bottom=bottom,
        x0=x0,
        x1=float(raw["x1"]),
        size=sizes.most_common(1)[0][0],
        bold=all(_BOLD_FONT.search(str(char["fontname"])) for char in chars if str(char["text"]).strip()),
        bullet=bullet is not None,
        indent=x0 if bullet is None else bullet,
    )


def _without_running_lines(contents: list[_PageContent]) -> list[_PageContent]:
    """The pages without their running headers and footers: the same text in the top or bottom margin of half the
    pages or more (at least two), page numbers aside."""

    def in_margin(content: _PageContent, line: _Line) -> bool:
        margin = content.height * _RUNNING_MARGIN
        return line.bottom <= margin or line.top >= content.height - margin

    def numbers_aside(line: _Line) -> str:
        return re.sub(r"\d+", "#", line.text)

    counts = Counter(
        text
        for content in contents
        for text in {numbers_aside(line) for line in content.lines if in_margin(content, line)}
    )
    running = {text for text, pages in counts.items() if pages >= max(2, len(contents) / 2)}
    return [
        replace(
            content,
            lines=[line for line in content.lines if not (in_margin(content, line) and numbers_aside(line) in running)],
        )
        for content in contents
    ]


def _indents(starts: Iterable[float]) -> tuple[float, ...]:
    """Where list items start, close starts taken as one, left to right."""
    indents: list[float] = []
    for start in sorted(starts):
        if not indents or start - indents[-1] > _SAME_INDENT_PT:
            indents.append(start)
    return tuple(indents)


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
        list_indents=_indents(  # a numbered heading ("1. Scope") is no list item
            line.indent for line in content.lines if _starts_list(line) and line.size not in heading_levels
        ),
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
        -_SAME_LINE_PT <= gap <= (previous.bottom - previous.top) * _PARAGRAPH_GAP_RATIO  # lowered text may overlap
        and abs(line.size - previous.size) <= _SAME_SIZE_PT
        and line.bold == previous.bold
        and not _starts_list(line)
    )


def _text_block(lines: list[_Line], layout: _Layout) -> TextBlock:
    first = lines[0]
    text = _joined(line.text for line in lines)
    if first.size in layout.heading_levels:
        kind, level = TextKind.HEADING, layout.heading_levels[first.size]
    elif _starts_list(first):
        depth = 1 + sum(1 for indent in layout.list_indents if indent < first.indent - _SAME_INDENT_PT)
        kind, level = TextKind.LIST_ITEM, min(depth, MAX_HEADING_LEVEL)
        text = _BULLET.sub("", text) or text  # the bullet glyph goes; Word draws its own. Numbers stay
    else:
        kind, level = TextKind.PARAGRAPH, 1
    return TextBlock(text=text, kind=kind, level=level, alignment=_alignment(lines, layout), bold=first.bold)


def _starts_list(line: _Line) -> bool:
    return line.bullet or _is_list_start(line.text)


def _is_list_start(text: str) -> bool:
    return bool(_BULLET.match(text) or _NUMBERED.match(text))


def _joined(pieces: Iterable[str]) -> str:
    """Lines as one text: a space between them, none after a word broken at a hyphen ("conversion-" "service")."""
    text = ""
    for piece in (" ".join(raw.split()) for raw in pieces):
        if piece:
            text = piece if not text else text + piece if _BROKEN_WORD.search(text) else f"{text} {piece}"
    return text


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
