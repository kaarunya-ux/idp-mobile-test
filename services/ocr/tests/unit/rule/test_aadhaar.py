"""The Aadhaar rule: twelve digits that pass the Verhoeff checksum, or a correctly masked number."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from extraction.rule.aadhaar import AADHAAR_RULE_ID, evaluate_aadhaar_number
from extraction.rule.checksum import calculate_verhoeff_check_digit

first_digit = st.sampled_from("23456789")
eleven_digits = st.builds(
    lambda head, tail: head + tail, first_digit, st.text(alphabet="0123456789", min_size=10, max_size=10)
)


@given(eleven_digits)
def test_passes_when_the_number_has_a_valid_check_digit(payload: str) -> None:
    number = payload[:11] + calculate_verhoeff_check_digit(payload[:11])

    assert evaluate_aadhaar_number(number).passed


@given(eleven_digits)
def test_fails_when_the_check_digit_is_wrong(payload: str) -> None:
    right = calculate_verhoeff_check_digit(payload[:11])
    wrong = next(digit for digit in "0123456789" if digit != right)

    outcome = evaluate_aadhaar_number(payload[:11] + wrong)

    assert outcome.passed is False
    assert outcome.rule_id == AADHAAR_RULE_ID


@pytest.mark.parametrize(
    "value", ["", "12345678901", "1234567890123", "012345678901", "112233445566", "2345 6789 0123", "23456789012a"]
)
def test_fails_when_the_shape_is_wrong(value: str) -> None:
    assert evaluate_aadhaar_number(value).passed is False


@pytest.mark.parametrize("masked", ["XXXX XXXX 1234", "XXXX XXXX 0000"])
def test_passes_when_only_the_last_four_digits_are_shown(masked: str) -> None:
    assert evaluate_aadhaar_number(masked).passed


@pytest.mark.parametrize(
    "masked", ["XXXX XXXX 123", "XXXX XXXX 12345", "xxxx xxxx 1234", "XXXXXXXX1234", "XXXX XXXX XXXX"]
)
def test_fails_when_the_masked_form_is_malformed(masked: str) -> None:
    assert evaluate_aadhaar_number(masked).passed is False


def test_does_not_quote_the_number_when_it_fails() -> None:
    outcome = evaluate_aadhaar_number("234567890123")

    assert "234567890123" not in (outcome.message or "")
