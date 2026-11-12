"""The driving licence accuracy set: the cards are invented and stable, the two layouts are as described, and scoring does what the report says."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accuracy"))
from dl import card_set, draw, report, score  # noqa: E402

DL_NUMBER = re.compile(r"[A-Z]{2}-?[0-9]{2}(?:19|20)[0-9]{2}[0-9]{7}")  # the service's rule for the licence number
TRUTH = {"license_number": "KA0120190001234", "name": "ANIL DEMOJI", "address": "12, SAMPLE NAGAR, TESTPUR, KARNATAKA 560001"}


def field(key: str, value: str | None, status: str = "ACCEPTED") -> dict:
    return {"key": key, "value": value, "status": status}


def answer(**changes: dict) -> dict:
    fields = {k: field(k, v) for k, v in TRUTH.items()} | changes
    return {"documentType": "driving_licence", "fields": list(fields.values())}


# --- the card set ----------------------------------------------------------------------------------------------------


def test_builds_100_cards_98_valid_and_2_invalid_when_the_set_is_built() -> None:
    cards = card_set.build_cards()
    valid = [c for c in cards if c.group == "normal"]

    assert len(cards) == 100 and len({c.card_id for c in cards}) == 100 and len(valid) == 98
    assert all(DL_NUMBER.fullmatch(c.truth()["license_number"]) for c in valid)
    assert not any(DL_NUMBER.fullmatch(c.truth()["license_number"]) for c in cards if c.group == "invalid_number")


def test_builds_the_same_cards_and_the_planned_mix_when_the_set_is_built_twice() -> None:
    cards = card_set.build_cards()
    singles = {e: sum(c.effects == (e,) for c in cards) for e in card_set.EFFECTS}

    assert cards == card_set.build_cards() and {c.font for c in cards} == set(card_set.FONTS)
    assert set(singles.values()) == {8} and sum(not c.effects for c in cards if c.group == "normal") == 10
    assert sum(len(c.effects) == 2 for c in cards) == 32


def test_ends_every_address_with_a_pincode_and_uses_several_states_when_cards_are_made() -> None:
    addresses = [c.truth()["address"] for c in card_set.build_cards()]

    assert all(re.search(r"[1-9][0-9]{5}$", a) for a in addresses)
    assert len({a.split(",")[-1].rsplit(" ", 1)[0].strip() for a in addresses}) >= 5


def test_splits_the_cards_evenly_between_side_by_side_and_two_pictures_when_the_set_is_built() -> None:
    layouts = [c.layout for c in card_set.build_cards()]

    assert layouts.count("side_by_side") == layouts.count("two_pictures") == 50


def test_sends_a_wide_picture_for_side_by_side_and_a_front_and_a_back_for_two_pictures_when_cards_are_clean() -> None:
    clean = [c for c in card_set.build_cards() if not c.effects]
    side, two = next(c for c in clean if c.layout == "side_by_side"), next(c for c in clean if c.layout == "two_pictures")
    wide, pair = draw.pictures(side), draw.pictures(two)

    assert len(wide) == 1 and wide[0].width > 2 * draw.W and wide[0].height == draw.H
    assert len(pair) == 2 and pair[0].tobytes() != pair[1].tobytes() and wide[0].tobytes() == draw.pictures(side)[0].tobytes()


def test_changes_the_picture_for_every_defect_when_the_defect_is_applied() -> None:
    cards = card_set.build_cards()
    for defect in card_set.EFFECTS:
        card = next(c for c in cards if c.effects == (defect,) and c.layout == "two_pictures")
        assert draw.pictures(card)[0].tobytes() != draw.front(card).tobytes()


# --- scoring ---------------------------------------------------------------------------------------------------------


def test_scores_every_field_correct_and_exact_when_the_answer_is_what_was_printed() -> None:
    cells = score.score_card(TRUTH, answer()).values()

    assert {c["outcome"] for c in cells} == {"correct"} and all(c["exact"] for c in cells)


def test_scores_the_number_correct_but_not_exact_when_the_service_keeps_the_space() -> None:
    cells = score.score_card(TRUTH, answer(license_number=field("license_number", "KA01 20190001234")))

    assert cells["license_number"]["outcome"] == "correct" and cells["license_number"]["exact"] is False


def test_separates_wrong_accepted_wrong_review_and_missing_when_values_differ() -> None:
    reply = answer(
        name=field("name", "Anil Demoji photograph"),
        address=field("address", "12 Sample Nagar Testpur Karnataka", "NEEDS_REVIEW"),
        license_number=field("license_number", None, "EMPTY"),
    )
    cells = score.score_card(TRUTH, reply)

    assert [cells[k]["outcome"] for k in ("name", "address", "license_number")] == ["wrong_accepted", "wrong_review", "missing"]
    assert cells["address"]["reviewed"] is True and cells["name"]["reviewed"] is False


def test_scores_an_invalid_number_correct_only_when_not_accepted_when_the_card_is_in_the_invalid_class() -> None:
    def outcome(value: str | None, status: str) -> str:
        reply = answer(license_number=field("license_number", value, status))
        return score.score_card(TRUTH, reply, "invalid_number")["license_number"]["outcome"]

    assert [outcome("KA010190001234", "NEEDS_REVIEW"), outcome(None, "EMPTY"), outcome("KA010190001234", "ACCEPTED")] == [
        "correct",
        "correct",
        "wrong_accepted",
    ]


def test_leaves_every_field_missing_when_there_is_no_answer_or_another_document_type() -> None:
    assert {c["outcome"] for c in score.score_card(TRUTH, None).values()} == {"missing"}
    assert {c["outcome"] for c in score.score_card(TRUTH, {"documentType": "pan_card", "fields": []}).values()} == {"missing"}


# --- the report ------------------------------------------------------------------------------------------------------


def test_reports_the_layouts_apart_and_lists_every_wrong_accepted_field_when_the_report_is_built() -> None:
    def row(card: str, layout: str, response: dict) -> dict:
        fields = score.score_card(TRUTH, response)
        return {"card": card, "group": "normal", "font": "sans", "layout": layout, "effects": ["glare"], "truth": TRUTH, "seconds": 5.0, "fields": fields}

    wrong = answer(address=field("address", "Transport Department 12, Sample Nagar"))
    text = report.build([row("D1", "side_by_side", wrong), row("D2", "two_pictures", answer())])

    assert "side_by_side (1 cards) | 3 | 67% | 67% | **1**" in text and "two_pictures (1 cards) | 3 | 100%" in text
    assert "| D1 | glare | address | Transport Department 12, Sample Nagar | 12, SAMPLE NAGAR, TESTPUR, KARNATAKA 560001 |" in text
