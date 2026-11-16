"""``ResultExporterPort``: extraction results as JSON, CSV or XML here; XLSX and DOCX in ``office`` (IDP-03 ch. 3.6).

Values are written as extraction-service stored them, so masked fields stay masked in every format.
"""

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

from conversion.conversion.domain.exceptions import InvalidOptionError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.results import ExportLayout, ExportOptions, ResultDocument
from conversion.conversion.infrastructure.export import office
from conversion.conversion.infrastructure.export.rows import header_and_rows

XML_NAMESPACE = "urn:idp:result:v1"
RESULTS_STEM = "results"


class ResultExporter:
    def export(
        self,
        results: Sequence[ResultDocument],
        target: Format,
        options: ExportOptions,
        output_dir: Path,
        thumbnails: Mapping[str, Path],
    ) -> tuple[Path, ...]:
        output_dir.mkdir(parents=True, exist_ok=True)
        _check_layout(target, options.layout)
        match target:
            case Format.JSON:
                return _json(results, options, output_dir)
            case Format.CSV:
                return (_csv(results, options, output_dir / f"{RESULTS_STEM}.csv"),)
            case Format.XLSX:
                return (office.xlsx(results, options, output_dir / f"{RESULTS_STEM}.xlsx"),)
            case Format.DOCX:
                return (office.docx_report(results, thumbnails, output_dir / f"{RESULTS_STEM}.docx"),)
            case Format.XML:
                return (_xml(results, output_dir / f"{RESULTS_STEM}.xml"),)
            case _:
                raise InvalidOptionError("target", f"results can't be exported as {target}")


_LAYOUTS: Mapping[Format, frozenset[ExportLayout]] = {
    Format.JSON: frozenset({ExportLayout.ARRAY, ExportLayout.ONE_FILE_PER_DOCUMENT}),
    Format.CSV: frozenset({ExportLayout.ONE_ROW_PER_DOCUMENT}),
    Format.XLSX: frozenset({ExportLayout.ONE_ROW_PER_DOCUMENT, ExportLayout.ONE_SHEET_PER_DOCUMENT}),
}


def _check_layout(target: Format, layout: ExportLayout | None) -> None:
    if layout is not None and layout not in _LAYOUTS.get(target, frozenset()):
        raise InvalidOptionError("layout", f"{layout} doesn't apply to {target} exports")


def _json(results: Sequence[ResultDocument], options: ExportOptions, folder: Path) -> tuple[Path, ...]:
    if options.layout is ExportLayout.ONE_FILE_PER_DOCUMENT:
        paths = []
        for number, result in enumerate(results, start=1):
            path = folder / f"{RESULTS_STEM}-{number:03d}.json"
            path.write_text(json.dumps(result.raw, ensure_ascii=False, indent=2), encoding="utf-8")
            paths.append(path)
        return tuple(paths)
    path = folder / f"{RESULTS_STEM}.json"
    path.write_text(json.dumps([r.raw for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return (path,)


def _csv(results: Sequence[ResultDocument], options: ExportOptions, path: Path) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:  # the BOM makes Excel read UTF-8
        csv.writer(stream).writerows(
            [_safe_cell(cell) for cell in row]
            for row in header_and_rows(results, include_confidence=options.include_confidence)
        )
    return path


def _safe_cell(value: object) -> object:
    """A value starting with = + - @ would run as a formula in Excel (CSV injection); a leading ' keeps it text."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _xml(results: Sequence[ResultDocument], path: Path) -> Path:
    ET.register_namespace("", XML_NAMESPACE)
    root = ET.Element(f"{{{XML_NAMESPACE}}}results")
    for result in results:
        document = ET.SubElement(root, f"{{{XML_NAMESPACE}}}document", name=result.name)
        if result.document_type:
            document.set("type", result.document_type)
        for field in result.fields:
            element = ET.SubElement(document, f"{{{XML_NAMESPACE}}}field", key=field.key, label=field.label)
            for name, value in (("confidence", field.confidence), ("status", field.status), ("source", field.source)):
                if value is not None:
                    element.set(name, str(value))
            element.set("masked", "true" if field.masked else "false")
            element.text = field.value
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
