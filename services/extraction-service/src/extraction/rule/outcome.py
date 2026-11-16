"""What a rule says about a value: the ``rule`` object of the extraction result (IDP-00 ch. 7.8).

A failed rule is normal flow for OCR output, so rules return a ``RuleOutcome`` instead of raising (PY-09). The message
is safe to store and show: it describes the expected shape and never contains the value (OBS-04).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleOutcome:
    """The result of applying one rule: its id (as written in the template), whether it passed, and why not."""

    rule_id: str
    passed: bool
    message: str | None = None

    @classmethod
    def passing(cls, rule_id: str) -> "RuleOutcome":
        """The value satisfies the rule."""
        return cls(rule_id=rule_id, passed=True)

    @classmethod
    def failing(cls, rule_id: str, message: str) -> "RuleOutcome":
        """The value breaks the rule; ``message`` says what was expected, without quoting the value."""
        return cls(rule_id=rule_id, passed=False, message=message)
