"""CSV <-> XLSX with openpyxl (IDP-03 ch. 3.4: ``xlsx_csv``).

CSV -> XLSX: the encoding and delimiter are detected, numbers are typed and shown as written, and IDs and codes
stay text. Ported from ``services/conversions/python_scripts/csv_convert.py`` (Sumukh, Karunya) without its AI
step. XLSX -> CSV: one sheet (the ``sheet`` option, or the first), values as stored, written as UTF-8.
"""

import csv
from collections.abc import Sequence
from pathlib import Path

from openpyxl import Workbook

from conversion.conversion.domain.cell_values import has_header
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.spreadsheets import (
    append_table,
    open_workbook,
    pick_sheet,
    read_csv,
    worksheet_rows,
)


class OpenpyxlCsvConverter:
    """Runs ``xlsx_csv`` in the direction the step asks for."""

    @property
    def converter(self) -> Converter:
        return Converter.XLSX_CSV

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs
        target = request.step.target
        if target is Representation.XLSX:
            output = request.output_dir / f"{source.stem}.xlsx"
            write_xlsx(read_csv(source), output)
        elif target is Representation.CSV:
            output = request.output_dir / f"{source.stem}.csv"
            write_csv_from_xlsx(source, output, request.options.sheet)
        else:
            raise ValueError(f"xlsx_csv can't write {target}")  # the route planner never asks for this
        return StepResult(outputs=(output,), page_count=1)  # a table counts as one page of usage


def write_xlsx(rows: Sequence[Sequence[str]], output: Path) -> None:
    """One sheet: a styled header row when the data has one, then typed cells. Streams rows (FIL-07)."""
    workbook = Workbook(write_only=True)
    append_table(workbook.create_sheet("Sheet1"), rows, header_rows=1 if rows and has_header(rows) else 0)
    workbook.save(output)


def write_csv_from_xlsx(source: Path, output: Path, sheet_name: str | None) -> None:
    """Export one sheet as UTF-8 CSV; trailing empty rows are left out."""
    with open_workbook(source) as workbook, output.open("w", encoding="utf-8", newline="") as stream:
        writer, pending_empty = csv.writer(stream), 0
        for cells in worksheet_rows(pick_sheet(workbook, sheet_name)):
            if not any(cells):
                pending_empty += 1
                continue
            writer.writerows([[]] * pending_empty)
            writer.writerow(cells)
            pending_empty = 0
