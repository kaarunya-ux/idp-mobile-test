"""Field acceptance: from what each engine read to one decision per field (IDP-03 ch. 2.6 stage 6, ch. 2.7)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from extraction.pipeline.domain.reading import Reading
from extraction.pipeline.domain.rules import apply_rule
from extraction.pipeline.domain.template import AcceptMode, FieldDefinition, FieldType, Mask
from extraction.rule import RuleOutcome, normalize_value, similarity
from idp_ocr_engines.model import Box

_PAIRS = (("A", "B"), ("A", "C"), ("B", "C"))


class FieldStatus(StrEnum):
    """``status`` of IDP-00 ch. 7.8 (REJECTED is set by people, not by the pipeline)."""

    ACCEPTED = "ACCEPTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    EMPTY = "EMPTY"


class FieldSource(StrEnum):
    """``source`` of IDP-00 ch. 7.8 that OCR can produce."""

    OCR_AGREE = "OCR_AGREE"
    OCR_2_OF_3 = "OCR_2_OF_3"


@dataclass(frozen=True, slots=True, kw_only=True)
class Thresholds:
    """Numbers from settings (ML-07)."""

    name_similarity: float
    review_confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldDecision:
    """The outcome for one field, with everything the result needs."""

    key: str
    label: str
    type: FieldType
    value: str | None
    normalized_value: str | None
    confidence: float
    source: FieldSource | None  # None: the engines did not agree, so no source can be claimed
    status: FieldStatus
    box: Box | None
    readings: Mapping[str, str | None]
    rule: RuleOutcome | None
    masked: bool


def decide_field(
    definition: FieldDefinition, readings: Mapping[str, Reading | None], *, thresholds: Thresholds, today: date
) -> FieldDecision:
    """Accept when two engines agree (equal, or similar enough for SIMILARITY fields) and the rule passes.

    An accepted field below ``review_confidence`` still needs review. Anything else is NEEDS_REVIEW with the best
    reading kept for the reviewer, or EMPTY when nothing was read and the field is optional.
    """
    present = {engine: reading for engine, reading in readings.items() if reading is not None}
    normalized = {
        engine: normalize_value(reading.text, definition.normalize, pattern=definition.rule.pattern)
        for engine, reading in present.items()
    }
    outcomes = {engine: apply_rule(definition.rule, text, today=today) for engine, text in normalized.items()}
    passing = [engine for engine in present if outcomes[engine].passed]
    shown = {engine: (reading.text if reading else None) for engine, reading in readings.items()}
    if not present:
        status = FieldStatus.NEEDS_REVIEW if definition.required else FieldStatus.EMPTY
        return _decision(definition, status=status, shown=shown)
    agreed = _agreement(definition, passing, normalized, thresholds)
    if agreed is not None:
        group, source = agreed
        best = max(group, key=lambda engine: present[engine].confidence)
        confidence = sum(present[engine].confidence for engine in group) / len(group)
        status = FieldStatus.ACCEPTED if confidence >= thresholds.review_confidence else FieldStatus.NEEDS_REVIEW
        return _decision(
            definition,
            status=status,
            shown=shown,
            source=source,
            engine=best,
            present=present,
            normalized=normalized,
            outcomes=outcomes,
            confidence=confidence,
        )
    pool = passing or list(present)
    best = max(pool, key=lambda engine: present[engine].confidence)
    return _decision(
        definition,
        status=FieldStatus.NEEDS_REVIEW,
        shown=shown,
        engine=best,
        present=present,
        normalized=normalized,
        outcomes=outcomes,
        confidence=present[best].confidence,
    )


def apply_mask(decision: FieldDecision, mask: Mask) -> FieldDecision:
    """Hide all but the last ``keep_last`` letters and digits everywhere the value appears (data minimisation)."""

    def hide(text: str | None) -> str | None:
        return None if text is None else _mask_text(text, mask)

    return FieldDecision(
        key=decision.key,
        label=decision.label,
        type=decision.type,
        value=hide(decision.value),
        normalized_value=hide(decision.normalized_value),
        confidence=decision.confidence,
        source=decision.source,
        status=decision.status,
        box=decision.box,
        readings={k: hide(v) for k, v in decision.readings.items()},
        rule=decision.rule,
        masked=True,
    )


def _mask_text(text: str, mask: Mask) -> str:
    keep_from = len([c for c in text if c.isalnum()]) - mask.keep_last
    seen = 0
    out: list[str] = []
    for char in text:
        if char.isalnum():
            out.append(char if seen >= keep_from else mask.char)
            seen += 1
        else:
            out.append(char)
    return "".join(out)


def _agreement(
    definition: FieldDefinition, passing: list[str], normalized: Mapping[str, str], thresholds: Thresholds
) -> tuple[list[str], FieldSource] | None:
    exact = definition.accept.mode is AcceptMode.EXACT
    threshold = definition.accept.threshold or thresholds.name_similarity
    for first, second in _PAIRS:
        if first not in passing or second not in passing:
            continue
        same = (
            normalized[first] == normalized[second]
            if exact
            else (similarity(normalized[first], normalized[second]) >= threshold)
        )
        if same:
            return [first, second], FieldSource.OCR_AGREE if (first, second) == _PAIRS[0] else FieldSource.OCR_2_OF_3
    return None


def _decision(  # noqa: PLR0913 - one constructor for the three shapes of outcome
    definition: FieldDefinition,
    *,
    status: FieldStatus,
    shown: Mapping[str, str | None],
    source: FieldSource | None = None,
    engine: str | None = None,
    present: Mapping[str, Reading] | None = None,
    normalized: Mapping[str, str] | None = None,
    outcomes: Mapping[str, RuleOutcome] | None = None,
    confidence: float = 0.0,
) -> FieldDecision:
    chosen = present[engine] if present and engine else None
    decision = FieldDecision(
        key=definition.key,
        label=definition.label,
        type=definition.type,
        value=chosen.text if chosen else None,
        normalized_value=normalized[engine] if normalized and engine else None,
        confidence=round(confidence, 4),
        source=source,
        status=status,
        box=chosen.box if chosen else None,
        readings=shown,
        rule=outcomes[engine] if outcomes and engine else None,
        masked=False,
    )
    return apply_mask(decision, definition.mask) if definition.mask else decision


__all__ = ["FieldDecision", "FieldSource", "FieldStatus", "Thresholds", "apply_mask", "decide_field"]
