"""One row of ``pipeline_runs`` per finished item (IDP-03 ch. 2.4): stage timings and how each field was decided,
for metrics and debugging. No field values: only counts (no document content outside the result, LOG-04)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from extraction.pipeline.domain.jobs import JobKind


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineRun:
    item_id: UUID
    account_id: UUID
    kind: JobKind
    document_type: str | None
    template_id: str | None
    template_version: int | None
    stages: Mapping[str, int]  # stage -> milliseconds
    decisions: Mapping[str, int]  # "<source>/<status>" -> number of fields, e.g. "OCR_AGREE/ACCEPTED": 4
    ai_calls: int
    duration_ms: int
    created_at: datetime


def stage_durations(starts: Sequence[tuple[str, float]], finished: float) -> dict[str, int]:
    """Milliseconds per stage, from the moment each stage first reported progress to the next one's."""
    ordered = sorted(starts, key=lambda s: s[1])
    ends = [start for _, start in ordered[1:]] + [finished]
    return {stage: max(0, round((end - start) * 1000)) for (stage, start), end in zip(ordered, ends, strict=True)}


def decision_counts(result: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in result.get("fields", []):
        key = f"{field.get('source') or 'NONE'}/{field.get('status')}"
        counts[key] = counts.get(key, 0) + 1
    return counts
