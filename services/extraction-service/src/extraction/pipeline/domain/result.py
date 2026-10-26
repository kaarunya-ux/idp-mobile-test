"""The extraction result, JSON schema v1 (IDP-00 ch. 7.8), built from decisions."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from extraction.pipeline.domain.classify import Classification
from extraction.pipeline.domain.decisions import FieldDecision, FieldStatus
from extraction.pipeline.domain.quality import QualityScores

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class PageSummary:
    """One page of the result: its size, where the image is stored, and how good the photo was."""

    index: int
    width: int
    height: int
    image_key: str | None
    quality: QualityScores
    quality_passed: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Which software produced the result (ML-04): the pipeline, each engine's models, and how long it took."""

    pipeline_version: str
    engines: Sequence[tuple[str, str]]  # (slot, version) such as ("ocr-a", "ppocrv6-small-det@...")
    duration_ms: int


def build_result(
    *,
    classification: Classification | None,
    pages: Sequence[PageSummary],
    decisions: Sequence[FieldDecision],
    provenance: Provenance,
) -> dict[str, Any]:
    """The result as the JSON object stored by document-service. Without a template there are no fields."""
    template = classification.template if classification else None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "documentType": {
            "code": template.document_type if template else None,
            "confidence": round(classification.confidence, 4) if classification else 0.0,
        },
        "template": {"id": template.template_id, "version": template.version, "scope": template.scope}
        if template
        else None,
        "pages": [_page(page) for page in pages],
        "fields": [_field(decision) for decision in decisions],
        "tables": [],
        "validation": _validation(decisions, classified=template is not None),
        "provenance": {
            "pipelineVersion": provenance.pipeline_version,
            "engines": [{"id": slot, "version": version} for slot, version in provenance.engines],
            "models": [],
            "durationMs": provenance.duration_ms,
        },
    }


def _page(page: PageSummary) -> dict[str, Any]:
    return {
        "index": page.index,
        "width": page.width,
        "height": page.height,
        "imageKey": page.image_key,
        "quality": {
            "blur": round(page.quality.blur, 2),
            "brightness": round(page.quality.brightness, 3),
            "glare": round(page.quality.glare, 4),
            "passed": page.quality_passed,
        },
    }


def _field(decision: FieldDecision) -> dict[str, Any]:
    box = decision.box
    return {
        "key": decision.key,
        "label": decision.label,
        "type": decision.type.value,
        "value": decision.value,
        "normalizedValue": decision.normalized_value,
        "displayValue": decision.normalized_value,
        "confidence": decision.confidence,
        "source": decision.source.value if decision.source else None,
        "status": decision.status.value,
        "page": 0,
        "box": [round(box.x, 4), round(box.y, 4), round(box.width, 4), round(box.height, 4)] if box else None,
        "readings": {"A": None, "B": None, "C": None, "ai": None} | dict(decision.readings),
        "rule": {"id": decision.rule.rule_id, "passed": decision.rule.passed, "message": decision.rule.message}
        if decision.rule
        else None,
        "masked": decision.masked,
    }


def _validation(decisions: Sequence[FieldDecision], *, classified: bool) -> dict[str, Any]:
    return {
        "fieldsTotal": len(decisions),
        "accepted": sum(d.status is FieldStatus.ACCEPTED for d in decisions),
        "needsReview": sum(d.status is FieldStatus.NEEDS_REVIEW for d in decisions) + (0 if classified else 1),
        "failedRules": sorted({d.rule.rule_id for d in decisions if d.rule and not d.rule.passed}),
    }
