"""The DATE rule: a value in one of the allowed formats, a real calendar date, inside a range (IDP-03 ch. 2.5).

Formats use the template's tokens ``dd``, ``MM`` and ``yyyy`` plus literal separators, for example ``dd/MM/yyyy``,
``ddMMyyyy`` (what is left after the DIGITS_ONLY normalizer) or ``yyyy`` for a year of birth. The range comes from
the caller, so "today" is injected and never read here (DOM-05).
"""

import re
from collections.abc import Sequence
from datetime import date

from extraction.rule.exceptions import UnsupportedDefinitionError
from extraction.rule.outcome import RuleOutcome

_TOKEN = re.compile(r"yyyy|MM|dd")
_GROUPS = {"yyyy": r"(?P<year>\d{4})", "MM": r"(?P<month>\d{2})", "dd": r"(?P<day>\d{2})"}
_SLASH_MISREAD_LENGTH = 10
_SLASH_POSITIONS = (2, 5)
_NOT_A_FORMAT = "The date is not in an accepted format."
_NOT_A_DATE = "The date does not exist in the calendar."
_OUT_OF_RANGE = "The date is outside the allowed range."


def evaluate_date(value: str, *, rule_id: str, formats: Sequence[str], earliest: date, latest: date) -> RuleOutcome:
    """Check ``value`` against the allowed ``formats`` and the inclusive range ``earliest`` to ``latest``.

    A year-only format (``yyyy``) is compared by year. A format that is not valid raises
    ``UnsupportedDefinitionError``: that is a mistake in the template, not in the document.
    """
    for date_format in formats:
        match = _compile_format(date_format).fullmatch(value)
        if match is None:
            continue
        return _judge(match, rule_id=rule_id, earliest=earliest, latest=latest)
    return RuleOutcome.failing(rule_id, _NOT_A_FORMAT)


def repair_slash_misread(digits: str) -> str:
    """Remove the two digits that OCR read in place of the slashes of ``dd/MM/yyyy``.

    Ten digits shaped ``dd1MM1yyyy`` become ``ddMMyyyy``; any other input is returned unchanged. Carried over from
    the prototype: IDP-03 ch. 2.5 has no normalizer for this, so it is not wired into templates yet.
    """
    if len(digits) == _SLASH_MISREAD_LENGTH and all(digits[position] == "1" for position in _SLASH_POSITIONS):
        return digits[:2] + digits[3:5] + digits[6:]
    return digits


def _compile_format(date_format: str) -> re.Pattern[str]:
    tokens = _TOKEN.findall(date_format)
    if tokens.count("yyyy") != 1 or len(tokens) != len(set(tokens)):
        message = f"The date format {date_format!r} must contain yyyy once and no token twice."
        raise UnsupportedDefinitionError(message)
    pattern = ""
    last = 0
    for token in _TOKEN.finditer(date_format):
        pattern += re.escape(date_format[last : token.start()]) + _GROUPS[token.group()]
        last = token.end()
    return re.compile(pattern + re.escape(date_format[last:]))


def _judge(match: re.Match[str], *, rule_id: str, earliest: date, latest: date) -> RuleOutcome:
    groups = match.groupdict()
    year = int(groups["year"])
    if groups.get("month") is None and groups.get("day") is None:
        in_range = earliest.year <= year <= latest.year
        return RuleOutcome.passing(rule_id) if in_range else RuleOutcome.failing(rule_id, _OUT_OF_RANGE)
    try:
        candidate = date(year, int(groups.get("month") or 1), int(groups.get("day") or 1))
    except ValueError:
        return RuleOutcome.failing(rule_id, _NOT_A_DATE)
    if earliest <= candidate <= latest:
        return RuleOutcome.passing(rule_id)
    return RuleOutcome.failing(rule_id, _OUT_OF_RANGE)
