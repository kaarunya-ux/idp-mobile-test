"""XLSX or CSV -> document model (IDP-03 ch. 3.4: "openpyxl sheets -> model").

Each sheet with data becomes a page holding one table, named after the sheet. Trailing empty rows and columns
are dropped. CSV files are read with the same encoding and delimiter detection as CSV -> XLSX.
"""

from collections.abc import Iterable

from openpyxl.worksheet._read_only import ReadOnlyWorksheet

from conversion.conversion.domain.cell_values import has_header
from conversion.conversion.domain.document_model import DocumentModel, Page, Table
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.spreadsheets import open_workbook, read_csv, worksheet_rows
from conversion.conversion.infrastructure.model_file import model_path, write_model


class SheetReader:
    """Writes ``<input>.model.json``: one page per sheet with data."""

    @property
    def converter(self) -> Converter:
        return Converter.SHEET_TO_MODEL

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs
        if request.step.source is Representation.CSV:
            pages = [_page(read_csv(source), name=None)]
        else:
            with open_workbook(source) as workbook:
                pages = [
                    _page(_trimmed(worksheet_rows(sheet)), name=sheet.title)
                    for sheet in workbook.worksheets
                    if isinstance(sheet, ReadOnlyWorksheet)  # chart-only sheets hold no cells
                ]
        kept = tuple(page for page in pages if page is not None)
        output = model_path(request.output_dir, source)
        write_model(DocumentModel(pages=kept), output)
        return StepResult(outputs=(output,), page_count=max(len(kept), 1))


def _page(rows: list[list[str]], *, name: str | None) -> Page | None:
    if not rows:
        return None
    table = Table.from_ragged(rows, header_rows=1 if has_header(rows) else 0)
    return Page(name=name, blocks=(table,))


def _trimmed(rows: Iterable[list[str]]) -> list[list[str]]:
    """Rows without trailing empty rows, cut to the last column that holds anything."""
    kept: list[list[str]] = []
    pending: list[list[str]] = []  # empty rows count only if something follows them
    for row in rows:
        if any(row):
            kept.extend(pending)
            kept.append(row)
            pending = []
        else:
            pending.append(row)
    width = max((index + 1 for row in kept for index, cell in enumerate(row) if cell), default=0)
    return [row[:width] for row in kept]
