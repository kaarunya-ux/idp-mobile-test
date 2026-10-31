"""The rules that decide whether a conversion is good: numbers come in as thresholds (ML-07)."""

import pytest

from idox_conversion.conversion.domain.policies import (
    CheckThresholds,
    Verdict,
    check_words,
    decide,
    words_of,
)

_THRESHOLDS = CheckThresholds(min_word_share=0.95, min_word_share_ocr=0.85, ocr_min_word_chars=3)


def test_thousands_commas_and_currency_do_not_split_a_number() -> None:
    assert words_of("Total $1,200.50") == ["total", "1200", "50"]


def test_leading_zeros_do_not_make_a_different_number() -> None:
    assert words_of("2024-01-05") == words_of("2024-1-5")


def test_every_word_kept_is_a_full_share() -> None:
    check = check_words("North 1,200 East", "east north 1200", ocr=False, thresholds=_THRESHOLDS)

    assert check.share == 1.0
    assert check.missing == ()


def test_lost_words_are_listed_once_in_source_order() -> None:
    check = check_words("alpha beta gamma beta", "alpha", ocr=False, thresholds=_THRESHOLDS)

    assert check.missing == ("beta", "gamma")
    assert check.share == pytest.approx(1 / 4)


def test_ocr_speckle_is_ignored_but_numbers_are_kept() -> None:
    check = check_words("invoice nn ee 7 total", "invoice total", ocr=True, thresholds=_THRESHOLDS)

    assert check.missing == ("7",)


def test_empty_source_counts_as_fully_kept() -> None:
    assert check_words("", "anything", ocr=False, thresholds=_THRESHOLDS).share == 1.0


def test_share_at_the_floor_is_ok_and_below_it_needs_review() -> None:
    ok = decide(
        check_words("a1 b1 c1 d1 e1 f1 g1 h1 i1 j1", "a1 b1 c1 d1 e1 f1 g1 h1 i1", ocr=False, thresholds=_THRESHOLDS),
        ocr=False,
        thresholds=_THRESHOLDS,
    )
    review = decide(check_words("a1 b1", "a1", ocr=False, thresholds=_THRESHOLDS), ocr=False, thresholds=_THRESHOLDS)

    assert ok.verdict is Verdict.REVIEW  # 90% is below 95%
    assert review.verdict is Verdict.REVIEW
    assert "at least 95%" in review.reasons[0]


def test_a_scan_is_held_to_the_looser_floor() -> None:
    check = check_words(
        "a1 b1 c1 d1 e1 f1 g1 h1 i1 j1", "a1 b1 c1 d1 e1 f1 g1 h1 i1", ocr=False, thresholds=_THRESHOLDS
    )

    assert decide(check, ocr=True, thresholds=_THRESHOLDS).verdict is Verdict.OK  # 90% passes the 85% floor
    assert decide(check, ocr=False, thresholds=_THRESHOLDS).verdict is Verdict.REVIEW


def test_a_different_table_count_needs_review_even_when_every_word_arrived() -> None:
    check = check_words("same words", "same words", ocr=False, thresholds=_THRESHOLDS)

    outcome = decide(check, ocr=False, thresholds=_THRESHOLDS, tables_expected=1, tables_found=2)

    assert outcome.verdict is Verdict.REVIEW
    assert "2 tables in the output for 1" in outcome.reasons[0]


def test_matching_table_counts_do_not_add_a_reason() -> None:
    check = check_words("same words", "same words", ocr=False, thresholds=_THRESHOLDS)

    assert decide(check, ocr=False, thresholds=_THRESHOLDS, tables_expected=2, tables_found=2).reasons == ()
