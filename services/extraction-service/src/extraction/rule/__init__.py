"""Business rules for field values: pure functions with no framework and no I/O (IDP-03 ch. 2.3, ``rule/``)."""

from extraction.rule.aadhaar import AADHAAR_RULE_ID, evaluate_aadhaar_number
from extraction.rule.checksum import calculate_verhoeff_check_digit, is_verhoeff_valid
from extraction.rule.dates import evaluate_date, repair_slash_misread
from extraction.rule.exceptions import UnsupportedDefinitionError
from extraction.rule.normalizers import Normalizer, normalize_value
from extraction.rule.outcome import RuleOutcome
from extraction.rule.pan import PAN_RULE_ID, evaluate_pan_number
from extraction.rule.pattern import evaluate_pattern
from extraction.rule.similarity import similarity

__all__ = [
    "AADHAAR_RULE_ID",
    "PAN_RULE_ID",
    "Normalizer",
    "RuleOutcome",
    "UnsupportedDefinitionError",
    "calculate_verhoeff_check_digit",
    "evaluate_aadhaar_number",
    "evaluate_date",
    "evaluate_pan_number",
    "evaluate_pattern",
    "is_verhoeff_valid",
    "normalize_value",
    "repair_slash_misread",
    "similarity",
]
