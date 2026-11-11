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
    "name": "ANIL TESTRAO",
    "dob": "05/06/1980",
    "gender": "MALE",
    "pincode": "560001",
    "address": "12, SAMPLE NAGAR, TESTPUR 560001",
}


def field(key: str, value: str | None, status: str = "ACCEPTED") -> dict:
    return {"key": key, "value": value, "status": status}


def answer(**changes: dict) -> dict:
    fields = {k: field(k, v) for k, v in TRUTH.items()} | changes
    return {"documentType": "aadhaar_card", "fields": list(fields.values())}


def outcomes(response: dict | None, group: str = "normal") -> dict[str, str]:
    return {k: v["outcome"] for k, v in score.score_card(TRUTH, response, group).items()}


# --- the card set ----------------------------------------------------------------------------------------------------


def test_builds_30_cards_28_with_a_valid_number_and_2_with_a_wrong_check_digit() -> None:
    cards = card_set.build_cards()

    assert len(cards) == 30 and len({c.card_id for c in cards}) == 30
    assert all(card_set.is_valid_number(c.number) for c in cards if c.group == "normal") and sum(c.group == "normal" for c in cards) == 28
    assert not any(card_set.is_valid_number(c.number) for c in cards if c.group == "wrong_checksum")


def test_builds_the_same_cards_every_time_and_covers_every_font_and_defect() -> None:
    cards = card_set.build_cards()

    assert cards == card_set.build_cards()
    assert {c.font for c in cards} == set(card_set.FONTS)
    assert {e for c in cards for e in c.effects} == set(card_set.EFFECTS)


def test_draws_the_same_picture_every_time_and_a_smaller_one_for_low_resolution() -> None:
    plain, lowres = card_set.build_cards()[0], next(c for c in card_set.build_cards() if "lowres" in c.effects)

    assert draw.card_picture(plain).tobytes() == draw.card_picture(plain).tobytes()
    assert draw.card_picture(lowres).size[0] < draw.W and draw.card_picture(plain).size == (draw.W, draw.H)


# --- scoring ---------------------------------------------------------------------------------------------------------


def test_scores_every_field_correct_when_the_answer_is_what_was_printed() -> None:
    assert set(outcomes(answer()).values()) == {"correct"}


def test_accepts_case_spacing_punctuation_and_date_format_differences() -> None:
    reply = answer(name=field("name", "Anil  Testrao"), dob=field("dob", "1980-06-05"), address=field("address", "12 Sample Nagar Testpur, 560001"))

    assert set(outcomes(reply).values()) == {"correct"}


def test_scores_a_different_value_wrong_a_missing_one_empty_and_a_review_as_review() -> None:
    reply = answer(name=field("name", "Meera Demoji"), gender=field("gender", None), dob=field("dob", "05/06/1980", "NEEDS_REVIEW"))

    assert (outcomes(reply)["name"], outcomes(reply)["gender"], outcomes(reply)["dob"]) == ("wrong", "empty", "review")
    assert score.score_card(TRUTH, reply)["dob"]["review_right"] is True


def test_scores_the_number_correct_only_in_the_masked_form_with_the_printed_last_four_digits() -> None:
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1008")))["aadhaar_number"] == "correct"
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "2345 6789 1008")))["aadhaar_number"] == "wrong"  # unmasked
    assert outcomes(answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009")))["aadhaar_number"] == "wrong"


def test_leaves_every_field_empty_when_there_is_no_answer_or_another_document_type() -> None:
    assert set(outcomes(None).values()) == {"empty"}
    assert set(outcomes({"documentType": "pan_card", "fields": answer()["fields"]}).values()) == {"empty"}


def test_scores_a_wrong_check_digit_number_correct_when_not_accepted_and_still_masked() -> None:
    held = answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009", "NEEDS_REVIEW"))
    accepted = answer(aadhaar_number=field("aadhaar_number", "XXXX XXXX 1009"))
    unmasked = answer(aadhaar_number=field("aadhaar_number", "2345 6789 1009", "NEEDS_REVIEW"))

    assert [outcomes(r, "wrong_checksum")["aadhaar_number"] for r in (held, accepted, unmasked)] == ["correct", "wrong", "wrong"]


# --- the report ------------------------------------------------------------------------------------------------------


def test_reports_counts_per_field_and_keeps_the_wrong_check_digit_cards_apart() -> None:
    def row(group: str, response: dict) -> dict:
        return {"card": "A", "group": group, "font": "sans", "effects": [], "seconds": 5.0, "fields": score.score_card(TRUTH, response, group)}

    text = report.build([row("normal", answer()), row("normal", answer(name=field("name", "Other Person"))), row("wrong_checksum", answer())])

    assert "| name | 2 | 1 | 1 | 0 | 0 (0) | 50% |" in text
    assert "Cards with a wrong check digit (1)" in text
