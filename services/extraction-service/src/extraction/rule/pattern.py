"""The REGEX rule: the whole value matches a regular expression (IDP-03 ch. 2.5, ``rule.kind`` REGEX)."""

import re

from extraction.rule.outcome import RuleOutcome


def evaluate_pattern(value: str, *, rule_id: str, pattern: str) -> RuleOutcome:
    """Check that all of ``value`` matches ``pattern``. A pattern that cannot compile is a template mistake."""
    if re.fullmatch(pattern, value):
        return RuleOutcome.passing(rule_id)
    return RuleOutcome.failing(rule_id, "The value does not have the expected shape.")
