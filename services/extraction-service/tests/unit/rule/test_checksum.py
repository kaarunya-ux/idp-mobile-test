"""The Verhoeff checksum: published examples, and the two guarantees the algorithm makes (IDP-03 ch. 2.13)."""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from extraction.rule.checksum import calculate_verhoeff_check_digit, is_verhoeff_valid

payloads = st.text(alphabet="0123456789", min_size=1, max_size=20)


def test_makes_check_digit_3_when_the_payload_is_236() -> None:
    assert calculate_verhoeff_check_digit("236") == "3"
    assert is_verhoeff_valid("2363")


@given(payloads)
def test_accepts_a_payload_when_its_own_check_digit_is_appended(payload: str) -> None:
    assert is_verhoeff_valid(payload + calculate_verhoeff_check_digit(payload))


@given(payloads, st.data())
def test_rejects_the_number_when_any_single_digit_is_changed(payload: str, data: st.DataObject) -> None:
    number = payload + calculate_verhoeff_check_digit(payload)
    index = data.draw(st.integers(min_value=0, max_value=len(number) - 1))
    replacement = data.draw(st.sampled_from([digit for digit in "0123456789" if digit != number[index]]))

    assert not is_verhoeff_valid(number[:index] + replacement + number[index + 1 :])


@given(payloads.filter(lambda text: len(text) >= 2), st.data())
def test_rejects_the_number_when_two_neighbouring_different_digits_are_swapped(
    payload: str, data: st.DataObject
) -> None:
    number = payload + calculate_verhoeff_check_digit(payload)
    index = data.draw(st.integers(min_value=0, max_value=len(number) - 2))
    assume(number[index] != number[index + 1])  # swapping equal digits changes nothing
    swapped = number[:index] + number[index + 1] + number[index] + number[index + 2 :]

    assert not is_verhoeff_valid(swapped)


@pytest.mark.parametrize("not_digits", ["", "12a4", " 123", "١٢٣٤", "12.4"])
def test_rejects_the_value_when_it_is_not_plain_digits(not_digits: str) -> None:
    assert not is_verhoeff_valid(not_digits)


def test_refuses_to_calculate_when_the_payload_is_not_digits() -> None:
    with pytest.raises(ValueError, match="digits only"):
        calculate_verhoeff_check_digit("12a")
