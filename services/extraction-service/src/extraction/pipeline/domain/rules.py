"""Runs the rule a template names on a normalised value."""

import re
from datetime import date

from extraction.pipeline.domain.template import RuleKind, RuleSpec
from extraction.rule import RuleOutcome, UnsupportedDefinitionError, evaluate_date, evaluate_pattern
from extraction.rule.aadhaar import evaluate_aadhaar_number
from extraction.rule.pan import evaluate_pan_number

_MASKED_AADHAAR = re.compile(r"X{8}([0-9]{4})")


def apply_rule(spec: RuleSpec, value: str, *, today: date) -> RuleOutcome:
    """Judge ``value``. A template that names something missing raises ``UnsupportedDefinitionError``."""
    if spec.kind is RuleKind.REGEX and spec.pattern is not None:
        return evaluate_pattern(value, rule_id=spec.id, pattern=spec.pattern)
    if spec.kind is RuleKind.DATE:
        return evaluate_date(
            value, rule_id=spec.id, formats=spec.formats, earliest=spec.earliest, latest=spec.latest or today
        )
    if spec.kind is RuleKind.CUSTOM and spec.validator == "PAN":
        return evaluate_pan_number(value)
    if spec.kind is RuleKind.CUSTOM and spec.validator == "AADHAAR":
        masked = _MASKED_AADHAAR.fullmatch(value)
        return evaluate_aadhaar_number(f"XXXX XXXX {masked.group(1)}" if masked else value)
    message = f"Rule '{spec.id}' ({spec.kind}) is not fully defined."
    raise UnsupportedDefinitionError(message)
