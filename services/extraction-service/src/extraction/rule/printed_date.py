"""The rule for a custom key of type ``date``: the value is a date, in whatever form the document prints it.

A custom key's value is returned as printed, never reformatted, so this rule only asks "is this a date?", not "which
number is the day?". It accepts numbers with the day or the month first (``20/10/2002``, ``10/20/2002``, ``10/26/32``),
the year first (``2002-10-20``), and month names (``20 OCT 2002``, ``20-Oct-2002``, ``October 20, 2002``). A form with
numbers only passes when at least one reading of it is a real calendar date. The standard cards keep their strict
DATE rule (``dates.py``).
"""

import re
from datetime import date

from extraction.rule.outcome import RuleOutcome

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?![a-z])"
)
_SEP = r"\s?[/.\-]\s?"
_YEAR = r"(?:\d{4}|\d{2})(?!\d)"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_WORDS_SEP = r"[\s.,/\-]*"

# Each form as one alternative; none has a capturing group, so a caller can wrap the whole in one.
PRINTED_DATE = (
    r"(?i:"
    rf"(?<!\d)\d{{4}}{_SEP}\d{{1,2}}{_SEP}\d{{1,2}}(?!\d)"  # 2002-10-20
    rf"|(?<!\d)\d{{1,2}}{_SEP}\d{{1,2}}{_SEP}{_YEAR}"  # 20/10/2002, 10/20/2002, 10/26/32
    rf"|(?<!\d){_DAY}{_WORDS_SEP}{_MONTHS}{_WORDS_SEP}{_YEAR}"  # 20 OCT 2002, 20-Oct-02
    rf"|(?<![a-z]){_MONTHS}{_WORDS_SEP}{_DAY}(?:,\s?|\s){_YEAR}"  # October 20, 2002
    r")"
)
_WHOLE = re.compile(rf"\s*{PRINTED_DATE}\s*")
_NUMBERS = re.compile(r"\d+")
_MONTH_NAME = re.compile(r"(?i)[a-z]{3}")
_MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
    )
}
_WITH_MONTH_NAME = 2  # numbers in a date with a month name: the day and the year
_YEAR_FIRST_LENGTH = 4  # digits of a year printed first (2002-10-20)
_TWO_DIGIT_YEARS = 100
_ANY_CENTURY = 2000  # a two-digit year is checked in a leap year, so 29/02/24 is not refused for want of a century
_NOT_A_DATE = "The value is not a date."


def evaluate_printed_date(value: str, *, rule_id: str) -> RuleOutcome:
    """Passes when ``value`` is a whole date in one of the printed forms above and some reading of it exists."""
    if _WHOLE.fullmatch(value) is None:
        return RuleOutcome.failing(rule_id, _NOT_A_DATE)
    digits = _NUMBERS.findall(value)
    month_name = _MONTH_NAME.search(re.sub(r"(?i)(?<=\d)(st|nd|rd|th)", "", value))
    if month_name is not None:  # the day and the year are the two numbers
        if len(digits) != _WITH_MONTH_NAME:
            return RuleOutcome.failing(rule_id, _NOT_A_DATE)
        day, year = (int(n) for n in digits)
        return _outcome(rule_id, [(year, _MONTH_NUMBERS[month_name.group().casefold()], day)])
    first, second, third = (int(n) for n in digits)
    if len(digits[0]) == _YEAR_FIRST_LENGTH:
        return _outcome(rule_id, [(first, second, third)])
    return _outcome(rule_id, [(third, second, first), (third, first, second)])  # day first, or month first


def _outcome(rule_id: str, readings: list[tuple[int, int, int]]) -> RuleOutcome:
    if any(_exists(*reading) for reading in readings):
        return RuleOutcome.passing(rule_id)
    return RuleOutcome.failing(rule_id, _NOT_A_DATE)


def _exists(year: int, month: int, day: int) -> bool:
    try:
        date(year if year >= _TWO_DIGIT_YEARS else _ANY_CENTURY + year, month, day)
    except ValueError:
        return False
    return True
