"""The PAN accuracy set: the cards are invented and stable, and scoring does what the report says. No service needed."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accuracy"))
from pan import card_set, draw, report, score  # noqa: E402

HOLDER_TYPES = "ABCFGHJLPT"  # what the service's PAN rule allows in position four
TRUTH = {"pan_number": "ABCPD1234E", "name": "ANIL DEMOJI", "father_name": "MEERA DEMOJI", "dob": "05/06/1980"}


def field(key: str, value: str | None, status: str = "ACCEPTED") -> dict:
    return {"key": key, "value": value, "status": status}


def answer(**changes: dict) -> dict:
    fields = {k: field(k, v) for k, v in TRUTH.items()} | changes
    return {"documentType": "pan_card", "fields": list(fields.values())}


def outcomes(response: dict | None, group: str = "normal") -> dict[str, str]:
    return {k: v["outcome"] for k, v in score.score_card(TRUTH, response, group).items()}


# --- the card set ----------------------------------------------------------------------------------------------------


def test_builds_100_cards_98_valid_and_2_invalid_when_the_set_is_built() -> None:
    cards = card_set.build_cards()
    valid = [c for c in cards if c.group == "normal"]

    assert len(cards) == 100 and len({c.card_id for c in cards}) == 100 and len(valid) == 98
    assert all(re.fullmatch(rf"[A-Z]{{3}}[{HOLDER_TYPES}][A-Z][0-9]{{4}}[A-Z]", c.number) for c in valid)
    assert all(c.number[3] not in HOLDER_TYPES for c in cards if c.group == "invalid_number")


def test_builds_the_same_cards_and_the_planned_mix_when_the_set_is_built_twice() -> None:
    cards = card_set.build_cards()
    singles = {e: sum(c.effects == (e,) for c in cards) for e in card_set.EFFECTS}

    assert cards == card_set.build_cards() and {c.font for c in cards} == set(card_set.FONTS)
    assert set(singles.values()) == {8} and sum(not c.effects for c in cards if c.group == "normal") == 10
    assert sum(len(c.effects) == 2 for c in cards) == 32


def test_uses_letters_that_ocr_confuses_when_names_and_numbers_are_made() -> None:
    names = " ".join(c.name for c in card_set.build_cards()).lower()
    numbers = "".join(c.number for c in card_set.build_cards())

    assert all(pair in names for pair in ("rn", "cl")) and "O" in numbers and "0" in numbers


def test_draws_the_same_picture_and_every_defect_changes_it_when_drawn_twice() -> None:
    cards = card_set.build_cards()
    plain = cards[0]

    assert draw.pictures(plain)[0].tobytes() == draw.pictures(plain)[0].tobytes() and draw.pictures(plain)[0].size == (draw.W, draw.H)
    for defect in card_set.EFFECTS:
        card = next(c for c in cards if c.effects == (defect,))
        assert draw.pictures(card)[0].tobytes() != draw.plain_card(card).tobytes()


# --- scoring ---------------------------------------------------------------------------------------------------------


def test_scores_every_field_correct_and_exact_when_the_answer_is_what_was_printed() -> None:
    cells = score.score_card(TRUTH, answer()).values()

    assert {c["outcome"] for c in cells} == {"correct"} and all(c["exact"] for c in cells)


def test_scores_correct_but_not_exact_when_case_or_the_date_format_differs() -> None:
    cells = score.score_card(TRUTH, answer(name=field("name", "Anil  Demoji"), dob=field("dob", "1980-06-05")))

    assert (cells["name"]["outcome"], cells["dob"]["outcome"]) == ("correct", "correct")
    assert (cells["name"]["exact"], cells["dob"]["exact"], cells["pan_number"]["exact"]) == (False, False, True)


def test_separates_wrong_accepted_wrong_review_and_missing_when_values_differ() -> None:
    reply = answer(
        name=field("name", "Meera Demoji"),
        father_name=field("father_name", "Anil Demoji", "NEEDS_REVIEW"),
        dob=field("dob", None, "NEEDS_REVIEW"),
        pan_number=field("pan_number", "ABCPD1Z34E"),
    )
    cells = score.score_card(TRUTH, reply)

    assert [cells[k]["outcome"] for k in ("name", "father_name", "dob", "pan_number")] == ["wrong_accepted", "wrong_review", "missing", "wrong_accepted"]
    assert cells["dob"]["reviewed"] is True and cells["name"]["reviewed"] is False  # review rate counts a held field whatever its value


def test_scores_an_invalid_number_correct_only_when_not_accepted_when_the_card_is_in_the_invalid_class() -> None:
    held = answer(pan_number=field("pan_number", "ABCZD1234E", "NEEDS_REVIEW"))
    empty = answer(pan_number=field("pan_number", None, "EMPTY"))
    accepted = answer(pan_number=field("pan_number", "ABCZD1234E"))

    assert [outcomes(r, "invalid_number")["pan_number"] for r in (held, empty, accepted)] == ["correct", "correct", "wrong_accepted"]


def test_leaves_every_field_missing_when_there_is_no_answer_or_another_document_type() -> None:
    assert set(outcomes(None).values()) == {"missing"}
    assert set(outcomes({"documentType": "aadhaar_card", "fields": answer()["fields"]}).values()) == {"missing"}


# --- the report ------------------------------------------------------------------------------------------------------


def test_lists_every_wrong_accepted_field_and_keeps_invalid_cards_apart_when_the_report_is_built() -> None:
    def row(group: str, response: dict, effects: list[str], card: str) -> dict:
        return {
            "card": card,
            "group": group,
            "font": "sans",
            "effects": effects,
            "truth": TRUTH,
            "seconds": 5.0,
            "fields": score.score_card(TRUTH, response, group),
        }

    wrong = answer(name=field("name", "Other Person"))
    text = report.build([row("normal", answer(), [], "P1"), row("normal", wrong, ["crop"], "P2"), row("invalid_number", answer(), [], "P3")])

    assert "| name | 2 | 50% | 50% | **1** | 0 | 0 | 0% |" in text and "| crop (1 cards) | 4 | 75% | 75% | **1** | 0 | 0 | 0% |" in text
    assert "| P2 | crop | name | Other Person | ANIL DEMOJI |" in text and "invalid number" not in text and "wrong shape (1)" in text
