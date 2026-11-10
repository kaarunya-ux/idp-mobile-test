"""prompt_check: is a new prompt version worse than the old one, judged from two golden summaries."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drift"))
import prompt_check  # noqa: E402

LIMITS = {"quality_drop_max": 0.0, "latency_ratio_max": 1.2, "tokens_ratio_max": 1.15}
OLD = {
    "conversion/digital": {"word_recall": 1.0, "cell_match": 1.0, "heading_recall": 1.0, "seconds_per_page_median": 20.0, "tokens_per_page_median": 400},
    "extraction/without_hints": {"normal_accuracy": 1.0, "hostile_obeyed": 1, "seconds_per_card_median": 5.0, "tokens_per_card_median": 79},
}


def _new(**changes: dict) -> dict:
    new = {name: dict(metrics) for name, metrics in OLD.items()}
    for name, metrics in changes.items():
        new[name.replace("__", "/")].update(metrics)
    return new


def test_says_the_new_prompt_is_the_same_when_nothing_moved() -> None:
    result = prompt_check.judge(OLD, _new(), [], [], LIMITS)

    assert result["status"] == "SAME"


def test_says_the_new_prompt_is_worse_when_quality_fell_in_any_class() -> None:
    result = prompt_check.judge(OLD, _new(conversion__digital={"cell_match": 0.9}), [], [], LIMITS)

    assert result["status"] == "WORSE"
    assert result["worse"] == ["conversion/digital: cell_match fell from 1.0 to 0.9"]


def test_says_the_new_prompt_is_worse_when_more_hostile_cards_were_obeyed() -> None:
    result = prompt_check.judge(OLD, _new(extraction__without_hints={"hostile_obeyed": 3}), [], [], LIMITS)

    assert result["status"] == "WORSE"
    assert "hostile cards obeyed went from 1 to 3" in result["worse"][0]


def test_says_the_new_prompt_is_worse_when_a_test_fails_that_passed_before() -> None:
    result = prompt_check.judge(OLD, _new(), ["G05_wide_table: cell match 0.5"], ["G05_wide_table: cell match 0.5", "G02_invoice: 0 tables"], LIMITS)

    assert result["status"] == "WORSE"
    assert result["worse"] == ["new test failure: G02_invoice: 0 tables"]


def test_says_the_new_prompt_is_better_when_it_improves_something_and_breaks_nothing() -> None:
    result = prompt_check.judge(OLD, _new(extraction__without_hints={"hostile_obeyed": 0}), ["G11: headings"], [], LIMITS)

    assert result["status"] == "BETTER"
    assert any("fixed" in item for item in result["better"])


def test_notes_a_slower_or_longer_prompt_without_calling_it_worse() -> None:
    result = prompt_check.judge(OLD, _new(conversion__digital={"seconds_per_page_median": 30.0, "tokens_per_page_median": 700}), [], [], LIMITS)

    assert result["status"] == "SAME"
    assert len(result["notes"]) == 2


def test_gives_the_pin_that_rolls_a_prompt_back() -> None:
    assert prompt_check.rollback_line("page-read", 2) == """PROMPTS__PINS='{"page-read": 2}'"""


def test_says_mixed_when_the_new_prompt_broke_one_thing_and_fixed_another() -> None:
    result = prompt_check.judge(OLD, _new(), ["G11: headings"], ["G12: headings"], LIMITS)

    assert result["status"] == "MIXED"
    assert result["worse"] == ["new test failure: G12: headings"]
    assert result["better"] == ["test failure fixed: G11: headings"]
