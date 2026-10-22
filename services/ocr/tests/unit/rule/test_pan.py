"""The PAN rule accepts only the five-letter, four-digit, one-letter shape with a valid holder type."""

import pytest

from extraction.rule.pan import PAN_RULE_ID, evaluate_pan_number


@pytest.mark.parametrize("value", ["ABCPK1234E", "FCLPM0001F", "AAACT9999Z"])
def test_passes_when_the_shape_is_valid(value: str) -> None:
    outcome = evaluate_pan_number(value)

    assert (outcome.rule_id, outcome.passed, outcome.message) == (PAN_RULE_ID, True, None)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ABCPK1234",  # too short
        "ABCPK1234EE",  # too long
        "abcpk1234e",  # lower case
        "ABCXK1234E",  # X is not a holder type
        "ABCPK12E4E",  # a letter where a digit belongs
        "AB1PK1234E",  # a digit where a letter belongs
        "ABCPK1234E ",  # trailing space
    ],
)
def test_fails_when_the_shape_is_wrong(value: str) -> None:
    outcome = evaluate_pan_number(value)

    assert outcome.passed is False
    assert outcome.message is not None


def test_does_not_quote_the_value_when_it_fails() -> None:
    outcome = evaluate_pan_number("ZZZZZ9999Z")

    assert "ZZZZZ9999Z" not in (outcome.message or "")
