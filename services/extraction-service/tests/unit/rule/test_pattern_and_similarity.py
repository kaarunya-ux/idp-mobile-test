import pytest
from hypothesis import given
from hypothesis import strategies as st

from extraction.rule import evaluate_pattern, similarity


def test_pattern_must_match_the_whole_value() -> None:
    assert evaluate_pattern("AB12", rule_id="r", pattern=r"[A-Z]{2}[0-9]{2}").passed
    assert not evaluate_pattern("AB123", rule_id="r", pattern=r"[A-Z]{2}[0-9]{2}").passed


def test_failure_message_does_not_quote_the_value() -> None:
    outcome = evaluate_pattern("SECRET", rule_id="r", pattern=r"[0-9]+")
    assert outcome.message is not None
    assert "SECRET" not in outcome.message


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [("ABC", "ABC", 1.0), ("", "", 1.0), ("ABC", "XYZ", 0.0), ("ABCD", "ABCE", 0.75), ("", "AB", 0.0)],
)
def test_similarity_examples(first: str, second: str, expected: float) -> None:
    assert similarity(first, second) == pytest.approx(expected)


@given(st.text(max_size=12), st.text(max_size=12))
def test_similarity_is_symmetric_and_between_zero_and_one(first: str, second: str) -> None:
    assert similarity(first, second) == pytest.approx(similarity(second, first))
    assert 0.0 <= similarity(first, second) <= 1.0
