"""The ONE_ROW_PER_DOCUMENT layout, shared by the CSV and XLSX exports (IDP-03 ch. 3.6)."""

from collections.abc import Sequence

from conversion.conversion.domain.results import ResultDocument


def field_keys(results: Sequence[ResultDocument]) -> list[str]:
    """Every field key across the documents, in first-seen order (IDP-03 ch. 3.6: the union)."""
    keys: dict[str, None] = {}
    for result in results:
        for field in result.fields:
            keys.setdefault(field.key, None)
    return list(keys)


def header_and_rows(results: Sequence[ResultDocument], *, include_confidence: bool) -> list[list[object]]:
    """ONE_ROW_PER_DOCUMENT: ``document``, ``documentType``, then each field (and its confidence, if asked)."""
    keys = field_keys(results)
    header: list[object] = ["document", "documentType"]
    for key in keys:
        header += [key, f"{key}_confidence"] if include_confidence else [key]
    rows = [header]
    for result in results:
        by_key = {field.key: field for field in result.fields}
        row: list[object] = [result.name, result.document_type or ""]
        for key in keys:
            field = by_key.get(key)
            row.append(field.value if field and field.value is not None else "")
            if include_confidence:
                row.append(field.confidence if field and field.confidence is not None else "")
        rows.append(row)
    return rows
