"""The normalizers named in IDP-03 ch. 2.5, including the position-aware OCR fix."""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from extraction.rule.exceptions import UnsupportedDefinitionError
from extraction.rule.normalizers import Normalizer, normalize_value

PAN_PATTERN = "^[A-Z]{5}[0-9]{4}[A-Z]$"


@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        (Normalizer.UPPER, "ab c", "AB C"),
        (Normalizer.LOWER, "AB C", "ab c"),
        (Normalizer.STRIP_SPACES, " A B\tC\n", "ABC"),
        (Normalizer.COLLAPSE_SPACES, "  A   B \t C ", "A B C"),
        (Normalizer.DIGITS_ONLY, "27 / 02-2002", "27022002"),
        (Normalizer.OCR_CONFUSABLES_NUMERIC, "O1lSB7", "011587"),
    ],
)
def test_applies_the_operation_when_it_is_listed(operation: Normalizer, value: str, expected: str) -> None:
    assert normalize_value(value, [operation]) == expected


def test_applies_the_operations_in_the_order_given() -> None:
    assert (
        normalize_value(" o1 ", [Normalizer.UPPER, Normalizer.STRIP_SPACES, Normalizer.OCR_CONFUSABLES_NUMERIC]) == "01"
    )


def test_fixes_letters_and_digits_by_position_when_the_pattern_is_a_pan() -> None:
    fixed = normalize_value("FCLPMOOO1F", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=PAN_PATTERN)

    assert fixed == "FCLPM0001F"


def test_fixes_digits_read_as_letters_when_the_pattern_expects_letters() -> None:
    assert normalize_value("5BC0K1234E", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=PAN_PATTERN) == "SBCOK1234E"


def test_changes_nothing_when_the_length_does_not_match_the_pattern() -> None:
    assert normalize_value("FCLPMOOO1", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=PAN_PATTERN) == "FCLPMOOO1"


def test_leaves_positions_alone_when_the_pattern_allows_both_kinds() -> None:
    assert normalize_value("O0O0", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern="^[A-Z0-9]{4}$") == "O0O0"


def test_understands_digit_shorthand_and_literals_when_reading_a_pattern() -> None:
    assert normalize_value("OO/1S", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=r"^\d{2}/[0-9]{2}$") == "00/15"


def test_reads_a_restricted_letter_class_as_letters_when_a_pattern_lists_holder_types() -> None:
    assert (
        normalize_value("A8C1K", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern="^[A-Z]{3}[ABCFGHJLPT][A-Z]$") == "ABCIK"
    )


@pytest.mark.parametrize("pattern", ["^[A-Z]+$", "^(AB|CD)$", "^[A-Z]{2,5}$", "^[A-Z]*$", "^A|B$"])
def test_raises_when_the_pattern_is_not_fixed_length(pattern: str) -> None:
    with pytest.raises(UnsupportedDefinitionError):
        normalize_value("ABC", [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=pattern)


def test_raises_when_the_pattern_is_missing() -> None:
    with pytest.raises(UnsupportedDefinitionError):
        normalize_value("ABC", [Normalizer.OCR_CONFUSABLES_ALNUM])


@given(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=10, max_size=10))
def test_gives_the_same_result_when_the_position_fix_is_applied_twice(value: str) -> None:
    once = normalize_value(value, [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=PAN_PATTERN)
    twice = normalize_value(once, [Normalizer.OCR_CONFUSABLES_ALNUM], pattern=PAN_PATTERN)

    assert once == twice


@given(st.text(max_size=40))
def test_gives_the_same_result_when_upper_case_and_space_stripping_are_applied_twice(value: str) -> None:
    operations = [Normalizer.UPPER, Normalizer.STRIP_SPACES]

    assert normalize_value(normalize_value(value, operations), operations) == normalize_value(value, operations)


@given(st.text(max_size=40))
def test_leaves_only_digits_when_digits_only_is_applied(value: str) -> None:
    assert re.fullmatch(r"[0-9]*", normalize_value(value, [Normalizer.DIGITS_ONLY]))


def test_drops_digits_of_other_scripts_when_only_ascii_digits_are_wanted() -> None:
    assert normalize_value("27/०२/2002 ٣", [Normalizer.DIGITS_ONLY]) == "272002"
