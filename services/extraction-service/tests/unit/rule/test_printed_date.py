"""A custom key's date: any printed form passes, since the value is returned as printed; a non-date fails."""

import pytest

from extraction.rule.printed_date import evaluate_printed_date


@pytest.mark.parametrize(
    "value",
    [
        "20 OCT 2002",
        "01 Jan 1920",
        "20-Oct-2002",
        "20th October 2002",
        "October 20, 2002",
        "Sept 3 2002",
        "20/10/2002",  # day first
        "10/20/2002",  # month first
        "10/26/32",  # month first, two-digit year
        "29/02/24",  # a two-digit year in a leap year
        "2002-10-20",
        "20.10.2002",
    ],
)
def test_passes_when_the_value_is_a_date_in_a_printed_form(value: str) -> None:
    assert evaluate_printed_date(value, rule_id="custom-date").passed


@pytest.mark.parametrize(
    "value",
    ["31 FEB 2002", "13/13/2002", "32/01/2002", "OCT 2002", "20 OCTOBERX 2002", "Sex", "12345", "20/10/2002 extra"],
)
def test_fails_when_the_value_is_not_a_real_date(value: str) -> None:
    outcome = evaluate_printed_date(value, rule_id="custom-date")

    assert not outcome.passed
    assert outcome.rule_id == "custom-date"
