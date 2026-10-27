"""Extraction results as ``EXPORT`` reads them (IDP-00 ch. 7.8), and the export options (IDP-03 ch. 3.6).

Only what an export shows is read; unknown keys are ignored (MSG-06). Values are taken as extraction-service wrote
them: a masked field is already masked there, and nothing here can unmask it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from conversion.conversion.domain.exceptions import InvalidOptionError, InvalidRequestError


class ExportLayout(StrEnum):
    ONE_ROW_PER_DOCUMENT = "ONE_ROW_PER_DOCUMENT"  # CSV and XLSX: a row per document, a column per field key
    ONE_SHEET_PER_DOCUMENT = "ONE_SHEET_PER_DOCUMENT"  # XLSX: a sheet per document
    ARRAY = "ARRAY"  # JSON: one file holding every result
    ONE_FILE_PER_DOCUMENT = "ONE_FILE_PER_DOCUMENT"  # JSON: a file per result


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportOptions:
    layout: ExportLayout | None  # None: the format's default
    include_confidence: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultField:
    key: str
    label: str
    value: str | None  # displayValue, else value: what a person reads
    confidence: float | None
    source: str | None
    status: str | None
    masked: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultTable:
    key: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultDocument:
    name: str  # the document id from the result's storage key, so rows can be traced back
    document_type: str | None
    fields: tuple[ResultField, ...]
    tables: tuple[ResultTable, ...]
    accepted: int
    needs_review: int
    failed_rules: tuple[str, ...]
    thumbnail_key: str | None  # the first page's image in idp-derived, for the DOCX and PDF reports
    raw: Mapping[str, Any]  # the result as stored, for JSON exports


def parse_export_options(raw: Mapping[str, object]) -> ExportOptions:
    layout = raw.get("layout")
    include = raw.get("includeConfidence", False)
    if layout is not None and layout not in ExportLayout.__members__:
        raise InvalidOptionError("layout", f"must be one of {', '.join(ExportLayout)}")
    if not isinstance(include, bool):
        raise InvalidOptionError("includeConfidence", "must be true or false")
    return ExportOptions(layout=ExportLayout(str(layout)) if layout else None, include_confidence=include)


def parse_result(data: object, *, name: str) -> ResultDocument:
    """One result file, already decoded from JSON. Raise ``InvalidRequestError`` if it isn't a v1 result."""
    if not isinstance(data, Mapping) or data.get("schemaVersion") != 1:
        raise InvalidRequestError("An input is not an extraction result (schema version 1).")
    validation = _mapping(data.get("validation"))
    pages = data.get("pages") or []
    first_page = _mapping(pages[0]) if isinstance(pages, list) and pages else {}
    return ResultDocument(
        name=name,
        document_type=_text(_mapping(data.get("documentType")).get("code")),
        fields=tuple(_field(_mapping(f)) for f in _items(data.get("fields"))),
        tables=tuple(_table(_mapping(t)) for t in _items(data.get("tables"))),
        accepted=_count(validation.get("accepted")),
        needs_review=_count(validation.get("needsReview")),
        failed_rules=tuple(str(r) for r in _items(validation.get("failedRules"))),
        thumbnail_key=_text(first_page.get("imageKey")),
        raw=data,
    )


def _field(field: Mapping[str, Any]) -> ResultField:
    value = field.get("displayValue", field.get("value"))
    confidence = field.get("confidence")
    return ResultField(
        key=str(field.get("key", "")),
        label=str(field.get("label") or field.get("key", "")),
        value=None if value is None else str(value),
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        source=_text(field.get("source")),
        status=_text(field.get("status")),
        masked=bool(field.get("masked", False)),
    )


def _table(table: Mapping[str, Any]) -> ResultTable:
    columns = tuple(str(_mapping(c).get("key", "")) for c in _items(table.get("columns")))
    rows = tuple(
        tuple("" if _mapping(cell).get("value") is None else str(_mapping(cell)["value"]) for cell in _items(row))
        for row in _items(table.get("rows"))
    )
    return ResultTable(key=str(table.get("key", "table")), columns=columns, rows=rows)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> Sequence[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
