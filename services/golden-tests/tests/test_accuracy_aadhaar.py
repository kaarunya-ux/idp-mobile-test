"""The Aadhaar accuracy set: the cards are invented and stable, and scoring does what the report says. No service needed."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "accuracy" / "aadhaar")]
import card_set  # noqa: E402
import draw  # noqa: E402
import report  # noqa: E402
import score  # noqa: E402

TRUTH = {
    "aadhaar_number": "XXXX XXXX 1008",
    "name": "Anil Testrao",
    "dob": "05/06/1980",
    "gender": "Male",
    "pincode": "560001",
    "address": "12, Sample Nagar, Testpur 560001",
}


def field(key: str, value: str | None, status: str = "ACCEPTED") -> dict:
    return {"key": key, "value": value, "status": status}


def answer(**changes: dict) -> dict:
    fields = {k: field(k, v) for k, v in TRUTH.items()} | changes
    return {"documentType": "aadhaar_card", "fields": list(fields.values())}


def outcomes(response: dict | None, group: str = "normal") -> dict[str, str]:
    return {k: v["outcome"] for k, v in score.score_card(TRUTH, response, group).items()}


# --- the card set ----------------------------------------------------------------------------------------------------


def test_builds_100_cards_98_with_a_valid_number_and_2_with_a_wrong_check_digit() -> None:
    cards = card_set.build_cards()

    assert len(cards) == 100 and len({c.card_id for c in cards}) == 100
    assert all(card_set.is_valid_number(c.number) for c in cards if c.group == "normal") and sum(c.group == "normal" for c in cards) == 98
    assert not any(card_set.is_valid_number(c.number) for c in cards if c.group == "wrong_checksum")


def test_builds_the_same_cards_every_time_and_covers_every_font_and_defect() -> None:
    cards = card_set.build_cards()

    assert cards == card_set.build_cards()
    assert {c.font for c in cards} == set(card_set.FONTS)
    assert all(sum(e in c.effects for c in cards) >= 8 for e in card_set.EFFECTS)


def test_uses_the_states_and_the_letters_that_ocr_confuses() -> None:
    text = " ".join(c.truth()["address"] + c.truth()["name"] for c in card_set.build_cards())

    assert all(state in text for state in ("Karnataka", "Haryana", "Chandigarh"))
    assert all(pair in text for pair in ("rn", "cl", "O-1", "1/10"))


def test_draws_the_same_picture_every_time_and_every_defect_changes_it() -> None:
    cards = card_set.build_cards()
    plain = cards[0]
    base = draw.plain_card(plain)

    assert draw.card_picture(plain).tobytes() == draw.card_picture(plain).tobytes() and base.size == (draw.W, draw.H)
    for defect in card_set.EFFECTS:
        card = next(c for c in cards if c.effects == (defect,))
        assert draw.card_picture(card).tobytes() != draw.plain_card(card).tobytes()
    assert draw.card_picture(next(c for c in cards if c.effects == ("crop",))).size[0] < draw.W
    assert draw.card_picture(next(c for c in cards if c.effects == ("lowres",))).size[0] < draw.W


# --- scoring ---------------------------------------------------------------------------------------------------------


def test_scores_every_field_correct_and_exact_when_the_answer_is_what_was_printed() -> None:
    cells = score.score_card(TRUTH, answer()).values()

    assert {c["outcome"] for c in cells} == {"correct"} and all(c["exact"] for c in cells)


def test_scores_case_and_date_format_differences_correct_but_not_exact() -> None:
    reply = answer(name=field("name", "ANIL  TESTRAO"), dob=field("dob", "1980-06-05"))
    cells = score.score_card(TRUTH, reply)

    assert (cells["name"]["outcome"], cells["dob"]["outcome"]) == ("correct", "correct")
    assert (cells["name"]["exact"], cells["dob"]["exact"], cells["gender"]["exact"]) == (False, False, True)


def test_separates_a_wrong_accepted_value_from_a_wrong_one_sent_to_review_and_a_missing_one() -> None:
    reply = answer(
        name=field("name", "Meera Demoji"),
        gender=field("gender", "Female", "NEEDS_REVIEW"),
        pincode=field("pincode", None),
        dob=field("dob", "05/06/1980", "NEEDS_REVIEW"),
    )
    cells = score.score_card(TRUTH, reply)

    assert [cells[k]["outcome"] for k in ("name", "gender", "pincode", "dob")] == ["wrong_accepted", "wrong_review", "missing", "correct"]
    assert cells["dob"]["reviewed"] is True and cells["name"]["reviewed"] is False  # a right value in review still counts in the review rate


def test_scores_the_number_correct_only_in_the_masked_form_with_the_printed_last_four_digits() -> None:
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1008")))["aadhaar_number"] == "correct"
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "2345 6789 1008")))["aadhaar_number"] == "wrong_accepted"  # unmasked
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009")))["aadhaar_number"] == "wrong_accepted"


def test_leaves_every_field_missing_when_there_is_no_answer_or_another_document_type() -> None:
    assert set(outcomes(None).values()) == {"missing"}
    assert set(outcomes({"documentType": "pan_card", "fields": answer()["fields"]}).values()) == {"missing"}


def test_scores_a_wrong_check_digit_number_correct_when_not_accepted_and_still_masked() -> None:
    held = answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009", "NEEDS_REVIEW"))
    accepted = answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009"))
    unmasked = answer(aadhaar_number=field("aadhaar_number", "2345 6789 1009", "NEEDS_REVIEW"))

    assert [outcomes(r, "wrong_checksum")["aadhaar_number"] for r in (held, accepted, unmasked)] == ["correct", "wrong_accepted", "wrong_review"]


# --- the report ------------------------------------------------------------------------------------------------------


def test_reports_per_field_and_per_defect_shows_wrong_accepted_apart_and_keeps_the_wrong_check_digit_cards_out() -> None:
    def row(group: str, response: dict, effects: list[str]) -> dict:
        return {
            "card": "A1",
            "group": group,
            "font": "sans",
            "effects": effects,
            "truth": TRUTH,
            "seconds": 5.0,
            "fields": score.score_card(TRUTH, response, group),
        }

    wrong = answer(name=field("name", "Other Person"))
    text = report.build([row("normal", answer(), []), row("normal", wrong, ["crop"]), row("wrong_checksum", answer(), [])])

    assert "| name | 2 | 50% | 50% | **1** | 0 | 0 | 0% |" in text
    assert "| crop (1 cards) | 6 | 83% | 83% | **1** | 0 | 0 | 0% |" in text
    assert "| A1 | crop | name | Other Person | Anil Testrao |" in text and "Cards with a wrong check digit (1)" in text
