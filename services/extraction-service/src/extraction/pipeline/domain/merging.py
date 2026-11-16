"""Combining results (IDP-03 ch. 2.6, last paragraph): a recheck's server reading with the on-device result, and
deep analysis with the stored result. Pure functions on result JSON (IDP-00 ch. 7.8).

Recheck: the device's settled fields stay; each weak field (the ones asked for, or else every field that isn't
ACCEPTED with enough confidence) takes the server's decision. Fields only the server found are added. If the server
sees a different document type, its result wins as a whole: the device matched the wrong template.

Deep analysis: the AI's findings go under ``analysis``; accepted fields are never overwritten.
"""

import copy
from collections.abc import Mapping, Sequence
from typing import Any

_ACCEPTED = "ACCEPTED"
_NEEDS_REVIEW = "NEEDS_REVIEW"


def weak_keys(result: Mapping[str, Any], *, asked: Sequence[str], review_confidence: float) -> list[str]:
    """The fields a recheck reads again: those asked for, or else every one not settled on the device."""
    if asked:
        return list(dict.fromkeys(asked))
    return [
        str(f["key"])
        for f in result.get("fields", [])
        if f.get("status") != _ACCEPTED or float(f.get("confidence") or 0.0) < review_confidence
    ]


def merge_recheck(
    on_device: Mapping[str, Any], server: Mapping[str, Any], *, asked: Sequence[str], review_confidence: float
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(server))
    device_type = (on_device.get("documentType") or {}).get("code")
    server_type = (server.get("documentType") or {}).get("code")
    if server_type is not None and device_type != server_type:
        merged["recheck"] = {"source": "ON_DEVICE", "checked": [], "changed": [], "documentTypeChanged": True}
        return merged
    weak = set(weak_keys(on_device, asked=asked, review_confidence=review_confidence))
    by_key = {str(f["key"]): f for f in server.get("fields", [])}
    fields, changed = [], []
    for field in on_device.get("fields", []):
        key = str(field["key"])
        replacement = by_key.pop(key, None) if key in weak else None
        if replacement is None:
            fields.append(copy.deepcopy(field))
            continue
        if replacement.get("value") != field.get("value"):
            changed.append(key)
        fields.append(copy.deepcopy(replacement))
    fields += [copy.deepcopy(f) for f in by_key.values() if str(f["key"]) not in {str(x["key"]) for x in fields}]
    merged["fields"] = fields
    merged["validation"] = _validation(fields)
    merged["recheck"] = {"source": "ON_DEVICE", "checked": sorted(weak), "changed": changed}
    return merged


def merge_analysis(
    result: Mapping[str, Any], analysis: Mapping[str, Any], *, model: str, prompt_version: str, question: str | None
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(result))
    accepted = {str(f["key"]) for f in result.get("fields", []) if f.get("status") == _ACCEPTED}
    merged["analysis"] = {
        "summary": analysis.get("summary"),
        "warnings": list(analysis.get("warnings", [])),
        "fields": [dict(f) for f in analysis.get("fields", []) if str(f.get("key")) not in accepted],
        "question": question,
        "model": {"id": model, "promptVersion": prompt_version},
    }
    provenance = merged.setdefault("provenance", {})
    provenance["models"] = [*provenance.get("models", []), {"id": model, "promptVersion": prompt_version}]
    return merged


def _validation(fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rules = [f.get("rule") or {} for f in fields]
    return {
        "fieldsTotal": len(fields),
        "accepted": sum(f.get("status") == _ACCEPTED for f in fields),
        "needsReview": sum(f.get("status") == _NEEDS_REVIEW for f in fields),
        "failedRules": sorted({str(rule.get("id")) for rule in rules if rule.get("passed") is False}),
    }
