"""Greedy CTC decoding: repeats collapse, blanks vanish, confidence is the geometric mean of the winners."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from idp_ocr_engines.ctc import decode_ctc_greedy, to_probabilities

CHARSET = ("", "a", "b", "c", " ")


def _one_hot(classes: list[int], *, sharpness: float = 20.0) -> np.ndarray:
    scores = np.zeros((len(classes), len(CHARSET)))
    scores[np.arange(len(classes)), classes] = sharpness
    return scores


def test_collapses_repeats_and_drops_blanks_when_decoding() -> None:
    text, confidence = decode_ctc_greedy(_one_hot([1, 1, 0, 1, 2, 2]), CHARSET)

    assert text == "aab"
    assert confidence > 0.99


def test_keeps_a_repeated_letter_when_a_blank_separates_it() -> None:
    assert decode_ctc_greedy(_one_hot([1, 0, 1]), CHARSET)[0] == "aa"


def test_returns_empty_text_and_zero_confidence_when_every_step_is_blank() -> None:
    assert decode_ctc_greedy(_one_hot([0, 0, 0]), CHARSET) == ("", 0.0)


def test_trims_the_text_when_the_decoded_string_has_outer_spaces() -> None:
    assert decode_ctc_greedy(_one_hot([4, 1, 4]), CHARSET)[0] == "a"


def test_gives_a_lower_confidence_when_the_winning_class_is_barely_ahead() -> None:
    sure = decode_ctc_greedy(_one_hot([1], sharpness=20.0), CHARSET)[1]
    unsure = decode_ctc_greedy(_one_hot([1], sharpness=0.5), CHARSET)[1]

    assert unsure < sure


def test_treats_scores_as_probabilities_when_the_rows_already_sum_to_one() -> None:
    probabilities = np.array([[0.1, 0.8, 0.05, 0.03, 0.02]])

    assert np.allclose(to_probabilities(probabilities), probabilities)
    assert decode_ctc_greedy(probabilities, CHARSET) == ("a", pytest.approx(0.8))


def test_applies_softmax_when_the_rows_are_raw_scores() -> None:
    result = to_probabilities(np.array([[2.0, 0.0, 0.0, 0.0, 0.0]]))

    assert result.sum() == pytest.approx(1.0)
    assert result[0, 0] > result[0, 1]


@given(st.text(alphabet="abc", min_size=1, max_size=30))
def test_returns_the_original_text_when_each_letter_is_followed_by_a_blank(original: str) -> None:
    classes = [CHARSET.index(letter) if position % 2 == 0 else 0 for letter in original for position in (0, 1)]

    assert decode_ctc_greedy(_one_hot(classes), CHARSET)[0] == original
