"""Word -> document model with python-docx (IDP-03 ch. 3.4: "python-docx tables -> model").

Reads paragraphs and tables in document order, no AI involved. Headings, list items and captions come from the
paragraph styles. Merged table cells appear once: python-docx repeats a merged cell's object across the cells it
spans, which duplicated content in the original scripts.
"""

import re
import zipfile
from pathlib import Path

import docx
from docx.document import Document as WordDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table as WordTable
from docx.text.paragraph import Paragraph

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
from conversion.conversion.domain.exceptions import UnreadableDocumentError
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.model_file import model_path, write_model

_HEADING_STYLE = re.compile(r"^Heading (\d)$")
_LIST_STYLE_LEVEL = re.compile(r"^List .* (\d)$")  # "List Bullet 2" -> depth 2
_ALIGNMENTS = {WD_ALIGN_PARAGRAPH.CENTER: Alignment.CENTER, WD_ALIGN_PARAGRAPH.RIGHT: Alignment.RIGHT}
_MIN_ROWS_FOR_A_HEADER = 2  # a header row needs at least one data row under it


class PythonDocxReader:
    """Writes ``<input>.model.json``: one page holding the document's text and tables in order."""

    @property
    def converter(self) -> Converter:
        return Converter.DOCX_TO_MODEL

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs
        document = _open(source)
        blocks: list[Block] = []
        for item in document.iter_inner_content():
            block = _table(item) if isinstance(item, WordTable) else _text_block(item)
            if block is not None:
                blocks.append(block)
        output = model_path(request.output_dir, source)
        write_model(DocumentModel(pages=(Page(blocks=tuple(blocks)),)), output)
        return StepResult(outputs=(output,), page_count=1)  # Word has no fixed pages until it is laid out


def _open(source: Path) -> WordDocument:
    try:
        return docx.Document(str(source))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:  # not a Word file
        raise UnreadableDocumentError from exc


def _text_block(paragraph: Paragraph) -> TextBlock | None:
    text = paragraph.text.strip()
    if not text:
        return None
    kind, level = _role(paragraph)
    runs = [run for run in paragraph.runs if run.text.strip()]
    return TextBlock(
        text=text,
        kind=kind,
        level=level,
        alignment=_alignment(paragraph),
        bold=bool(runs) and all(run.bold for run in runs),
    )


def _role(paragraph: Paragraph) -> tuple[TextKind, int]:
    style = paragraph.style.name if paragraph.style is not None and paragraph.style.name else ""
    if style == "Title":
        return TextKind.HEADING, 1
    if heading := _HEADING_STYLE.match(style):
        return TextKind.HEADING, min(max(int(heading.group(1)), 1), MAX_HEADING_LEVEL)
    if style == "Caption":
        return TextKind.CAPTION, 1
    numbering_level = _numbering_level(paragraph)
    if style.startswith("List") or numbering_level is not None:
        styled = _LIST_STYLE_LEVEL.match(style)
        depth = numbering_level if numbering_level is not None else int(styled.group(1)) if styled else 1
        return TextKind.LIST_ITEM, min(depth, MAX_HEADING_LEVEL)
    return TextKind.PARAGRAPH, 1


def _numbering_level(paragraph: Paragraph) -> int | None:
    """List depth from Word's numbering properties; python-docx has no public API for it."""
    properties = paragraph._p.pPr  # numbering is only reachable through the XML element
    numbering = properties.find(qn("w:numPr")) if properties is not None else None
    level = numbering.find(qn("w:ilvl")) if numbering is not None else None
    value = level.get(qn("w:val")) if level is not None else None
    return int(value) + 1 if value is not None and value.isdigit() else None


def _alignment(paragraph: Paragraph) -> Alignment:
    """The paragraph's own alignment, else its style's, else left."""
    word_alignment = paragraph.alignment or _style_alignment(paragraph)
    return _ALIGNMENTS.get(word_alignment, Alignment.LEFT) if word_alignment is not None else Alignment.LEFT


def _style_alignment(paragraph: Paragraph) -> WD_ALIGN_PARAGRAPH | None:
    style = paragraph.style
    return style.paragraph_format.alignment if style is not None else None


def _table(table: WordTable) -> Table | None:
    seen: list[object] = []  # the underlying XML cells already read; a merged cell repeats the same one
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            element = cell._tc  # the XML element identifies a merged cell across positions
            cells.append("" if any(element is known for known in seen) else cell.text.strip())
            seen.append(element)
        rows.append(cells)
    if not any(any(row) for row in rows):
        return None
    return Table.from_ragged(rows, header_rows=_header_rows(table))


def _header_rows(table: WordTable) -> int:
    """Rows Word repeats as a header on each page; failing that, a bold first row counts as one header row."""
    marked = 0
    for row in table.rows:
        properties = row._tr.trPr  # "repeat as header row" is only reachable through the XML
        if properties is None or properties.find(qn("w:tblHeader")) is None:
            break
        marked += 1
    if marked or len(table.rows) < _MIN_ROWS_FOR_A_HEADER:
        return marked
    first_runs = [run for cell in table.rows[0].cells for p in cell.paragraphs for run in p.runs if run.text.strip()]
    return 1 if first_runs and all(run.bold for run in first_runs) else 0
