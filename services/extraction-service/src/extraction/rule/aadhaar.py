"""The Aadhaar number rule: twelve digits, not starting with 0 or 1, ending in a valid Verhoeff check digit.

A card that prints only the last four digits (``XXXX XXXX 1234``) cannot be checksummed; its shape is checked and it
passes, because showing a masked number is allowed (IDP-00 ch. 8).
"""

import re

from extraction.rule.checksum import is_verhoeff_valid
from extraction.rule.outcome import RuleOutcome

AADHAAR_RULE_ID = "aadhaar-verhoeff"
_PLAIN = re.compile(r"[2-9][0-9]{11}")
_MASKED = re.compile(r"XXXX XXXX [0-9]{4}")
_NOT_TWELVE_DIGITS = "Expected twelve digits that do not start with 0 or 1."
_BAD_CHECKSUM = "The Aadhaar checksum does not match."


def evaluate_aadhaar_number(value: str) -> RuleOutcome:
    """Check a number with no spaces (twelve digits) or in the masked form ``XXXX XXXX 1234``."""
    if _MASKED.fullmatch(value):
        return RuleOutcome.passing(AADHAAR_RULE_ID)
    if not _PLAIN.fullmatch(value):
        return RuleOutcome.failing(AADHAAR_RULE_ID, _NOT_TWELVE_DIGITS)
    if not is_verhoeff_valid(value):
        return RuleOutcome.failing(AADHAAR_RULE_ID, _BAD_CHECKSUM)
    return RuleOutcome.passing(AADHAAR_RULE_ID)
