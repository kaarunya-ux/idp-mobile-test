"""The PAN number rule: ``AAAPA9999A`` (five letters, four digits, one letter)."""

import re

from extraction.rule.outcome import RuleOutcome

PAN_RULE_ID = "pan-format"
# Position four is the holder type (person, company, firm, ...). IDP-03 ch. 2.5 shows the looser pattern
# ^[A-Z]{5}[0-9]{4}[A-Z]$; this stricter list was carried over from the prototype and is flagged for confirmation.
_HOLDER_TYPES = "ABCFGHJLPT"
_PAN = re.compile(rf"[A-Z]{{3}}[{_HOLDER_TYPES}][A-Z][0-9]{{4}}[A-Z]")
_EXPECTED = "Expected five letters, four digits and a letter, with a valid holder-type letter in position four."


def evaluate_pan_number(value: str) -> RuleOutcome:
    """Check the shape of a PAN. The value should already be upper-case with no spaces."""
    if _PAN.fullmatch(value):
        return RuleOutcome.passing(PAN_RULE_ID)
    return RuleOutcome.failing(PAN_RULE_ID, _EXPECTED)
