"""Scoring a template test run (IDP-03 ch. 2.9): each sample's result against the values a person said are right.

A sample that couldn't be read at all is listed under ``failedSamples`` and left out of the scores. Values are
compared after dropping case, spaces and punctuation (``ABC-12 3`` = ``abc123``). A masked field (Aadhaar)
keeps only its last digits in the result, so only those are compared. The report keeps up to five confusions per
field, for the designer; they hold sample values, so the report is visible only to the template's account.

Publishing needs every required field at ``min_accuracy`` or better (``report_passes``); a required field with no
expected value in any sample can't pass.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from extraction.pipeline.domain.template import TemplateDefinition

MAX_CONFUSIONS = 5


@dataclass(frozen=True, slots=True, kw_only=True)
class SampleOutcome:
    sample_id: str
    expected: Mapping[str, str]
    result: Mapping[str, Any] | None  # None when the sample couldn't be read
    error_code: str | None = None


def canonical(text: str | None) -> str:
    return "".join(c for c in (text or "").upper() if c.isalnum())


def score_samples(template: TemplateDefinition, outcomes: Sequence[SampleOutcome]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {
        f.key: {"required": f.required, "correct": 0, "total": 0, "confusions": []} for f in template.fields
    }
    keep_last = {f.key: f.mask.keep_last for f in template.fields if f.mask is not None}
    for outcome in outcomes:
        if outcome.result is None:  # unreadable (a blurred photo): listed in failedSamples, not scored
            continue
        found = {str(f["key"]): f for f in outcome.result.get("fields", [])}
        for key, expected in outcome.expected.items():
            if key not in fields:
                continue
            got = found.get(key, {}).get("normalizedValue") or found.get(key, {}).get("value")
            entry = fields[key]
            entry["total"] += 1
            if _same(expected, got, keep_last.get(key)):
                entry["correct"] += 1
            elif len(entry["confusions"]) < MAX_CONFUSIONS:
                entry["confusions"].append({"sampleId": outcome.sample_id, "expected": expected, "got": got})
    for entry in fields.values():
        entry["accuracy"] = round(entry["correct"] / entry["total"], 4) if entry["total"] else None
    totals = [e for e in fields.values() if e["total"]]
    return {
        "samples": len(outcomes),
        "failedSamples": [{"sampleId": o.sample_id, "code": o.error_code} for o in outcomes if o.result is None],
        "fields": fields,
        "accuracy": round(sum(e["correct"] for e in totals) / sum(e["total"] for e in totals), 4) if totals else None,
    }


def report_passes(report: Mapping[str, Any], *, min_accuracy: float) -> bool:
    required = [e for e in report.get("fields", {}).values() if e.get("required")]
    return bool(required) and all(e.get("accuracy") is not None and e["accuracy"] >= min_accuracy for e in required)


def _same(expected: str, got: str | None, keep_last: int | None) -> bool:
    want, have = canonical(expected), canonical(got)
    if keep_last is not None:
        return bool(have) and want[-keep_last:] == have[-keep_last:]
    return want == have
