"""The DATE rule: format, real calendar date and range, with "today" passed in (DOM-05)."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from extraction.rule.dates import evaluate_date, repair_slash_misread
from extraction.rule.exceptions import UnsupportedDefinitionError
from extraction.rule.outcome import RuleOutcome

EARLIEST = date(1900, 1, 1)
TODAY = date(2026, 10, 21)


def _check(value: str, *formats: str) -> RuleOutcome:
    return evaluate_date(value, rule_id="dob-date", formats=formats, earliest=EARLIEST, latest=TODAY)


def test_passes_when_the_date_is_real_and_in_range() -> None:
    assert _check("27/02/2002", "dd/MM/yyyy").passed


def test_passes_when_the_digits_have_no_separators() -> None:
    assert _check("27022002", "ddMMyyyy").passed


@pytest.mark.parametrize("value", ["31/02/2000", "29/02/2001", "00/01/2000", "10/13/2000"])
def test_fails_when_the_date_does_not_exist(value: str) -> None:
    outcome = _check(value, "dd/MM/yyyy")

    assert outcome.passed is False
    assert outcome.message == "The date does not exist in the calendar."


def test_passes_on_a_leap_day_when_the_year_is_a_leap_year() -> None:
    assert _check("29/02/2000", "dd/MM/yyyy").passed


@pytest.mark.parametrize("value", ["31/12/1899", "22/10/2026"])
def test_fails_when_the_date_is_outside_the_range(value: str) -> None:
    assert _check(value, "dd/MM/yyyy").message == "The date is outside the allowed range."


def test_passes_on_the_last_allowed_day_when_it_is_today() -> None:
    assert _check("21/10/2026", "dd/MM/yyyy").passed


@pytest.mark.parametrize("value", ["", "27-02-2002", "2002/02/27", "27/2/2002", "27/02/02", "27/02/2002x"])
def test_fails_when_no_format_matches(value: str) -> None:
    assert _check(value, "dd/MM/yyyy").message == "The date is not in an accepted format."


def test_passes_when_any_listed_format_matches() -> None:
    assert _check("27-02-2002", "dd/MM/yyyy", "dd-MM-yyyy").passed


def test_compares_by_year_when_the_format_is_a_year_only() -> None:
    assert _check("1995", "yyyy").passed
    assert _check("2027", "yyyy").passed is False
    assert _check("1899", "yyyy").passed is False


@given(st.dates(min_value=EARLIEST, max_value=TODAY))
def test_passes_when_any_date_in_range_is_written_in_the_format(day: date) -> None:
    assert _check(day.strftime("%d/%m/%Y"), "dd/MM/yyyy").passed


@given(st.dates(min_value=TODAY + timedelta(days=1), max_value=date(2999, 12, 31)))
def test_fails_when_any_date_after_today_is_written_in_the_format(day: date) -> None:
    assert _check(day.strftime("%d/%m/%Y"), "dd/MM/yyyy").passed is False


@pytest.mark.parametrize("bad_format", ["dd/MM", "yyyy/yyyy", "dd/dd/yyyy", ""])
def test_raises_when_the_format_is_not_usable(bad_format: str) -> None:
    with pytest.raises(UnsupportedDefinitionError):
        _check("27/02/2002", bad_format)


@pytest.mark.parametrize(("digits", "expected"), [("1210712002", "12072002"), ("2710212002", "27022002")])
def test_removes_the_misread_slashes_when_ten_digits_have_a_one_at_positions_two_and_five(
    digits: str, expected: str
) -> None:
    assert repair_slash_misread(digits) == expected


@pytest.mark.parametrize("digits", ["12072002", "1220712002", "121071200", "", "27022002"])
def test_returns_the_digits_unchanged_when_they_do_not_have_that_shape(digits: str) -> None:
    assert repair_slash_misread(digits) == digits
