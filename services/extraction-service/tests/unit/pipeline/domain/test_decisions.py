from datetime import date

import pytest

from extraction.pipeline.domain.decisions import FieldSource, FieldStatus, Thresholds, apply_mask, decide_field
from extraction.pipeline.domain.reading import Reading
from extraction.pipeline.domain.standard_templates import AADHAAR_CARD, PAN_CARD
from extraction.pipeline.domain.template import Mask
from idp_ocr_engines.model import Box

TODAY = date(2026, 10, 21)
THRESHOLDS = Thresholds(name_similarity=0.95, review_confidence=0.85)
PAN = PAN_CARD.fields[0]
NAME = PAN_CARD.fields[1]
BOX = Box(x=0.1, y=0.1, width=0.2, height=0.05)


def read(text: str, confidence: float = 0.95) -> Reading:
    return Reading(text=text, confidence=confidence, box=BOX)


def decide(definition, **readings):  # type: ignore[no-untyped-def]
    return decide_field(definition, readings, thresholds=THRESHOLDS, today=TODAY)


def test_two_engines_that_agree_and_pass_the_rule_are_accepted() -> None:
    decision = decide(PAN, A=read("ABCPE1234F"), B=read("ABCPE1234F", 0.99))
    assert decision.status is FieldStatus.ACCEPTED
    assert decision.source is FieldSource.OCR_AGREE
    assert decision.confidence == pytest.approx(0.97)
    assert decision.value == "ABCPE1234F"


def test_ocr_confusables_are_fixed_before_comparing() -> None:
    decision = decide(PAN, A=read("ABCPE I234F"), B=read("abcpe1234f"))
    assert decision.status is FieldStatus.ACCEPTED
    assert decision.normalized_value == "ABCPE1234F"


def test_disagreement_needs_review_and_keeps_the_best_passing_reading() -> None:
    decision = decide(PAN, A=read("ABCPE1234F", 0.9), B=read("ABCPE1235F", 0.8))
    assert decision.status is FieldStatus.NEEDS_REVIEW
    assert decision.source is None
    assert decision.normalized_value == "ABCPE1234F"
    assert decision.readings == {"A": "ABCPE1234F", "B": "ABCPE1235F"}


def test_third_engine_breaks_the_tie_as_two_of_three() -> None:
    decision = decide(PAN, A=read("ABCPE1234F"), B=read("ABCPE1235F"), C=read("ABCPE1234F"))
    assert decision.status is FieldStatus.ACCEPTED
    assert decision.source is FieldSource.OCR_2_OF_3


def test_agreeing_engines_with_a_failing_rule_are_not_accepted() -> None:
    decision = decide(PAN, A=read("ABCDE1234F"), B=read("ABCDE1234F"))  # holder type D is not allowed
    assert decision.status is FieldStatus.NEEDS_REVIEW
    assert decision.rule is not None
    assert not decision.rule.passed


def test_low_confidence_needs_review_even_when_engines_agree() -> None:
    decision = decide(PAN, A=read("ABCPE1234F", 0.5), B=read("ABCPE1234F", 0.6))
    assert decision.status is FieldStatus.NEEDS_REVIEW
    assert decision.source is FieldSource.OCR_AGREE


def test_one_engine_alone_cannot_accept_a_field() -> None:
    assert decide(PAN, A=read("ABCPE1234F"), B=None).status is FieldStatus.NEEDS_REVIEW


def test_nothing_read_is_needs_review_for_a_required_field_and_empty_for_an_optional_one() -> None:
    assert decide(PAN, A=None, B=None).status is FieldStatus.NEEDS_REVIEW
    optional = type(PAN)(**{**{f: getattr(PAN, f) for f in PAN.__slots__}, "required": False})
    assert decide(optional, A=None, B=None).status is FieldStatus.EMPTY


def test_similar_names_are_accepted_and_different_names_are_not() -> None:
    close = decide(NAME, A=read("JAGDISH PRASAD MANDLOI"), B=read("JAGDISH PRASAD MANDLOl"))  # 1 edit in 22
    assert close.status is FieldStatus.ACCEPTED
    short = decide(NAME, A=read("ARJUN MANDLOI"), B=read("ARJUN MANDLOl"))  # 1 edit in 13 is below 0.95
    assert short.status is FieldStatus.NEEDS_REVIEW
    far = decide(NAME, A=read("ARJUN MANDLOI"), B=read("ARJAN MANDLOT"))
    assert far.status is FieldStatus.NEEDS_REVIEW


def test_aadhaar_number_is_masked_everywhere() -> None:
    number = AADHAAR_CARD.fields[0]
    decision = decide(number, A=read("2345 6789 0124"), B=read("234567890124"))
    assert decision.masked
    assert decision.value == "XXXX XXXX 0124"
    assert decision.normalized_value == "XXXXXXXX0124"
    assert all(v in {"XXXX XXXX 0124", "XXXXXXXX0124"} for v in decision.readings.values() if v)


def test_masking_keeps_separators_and_the_last_characters() -> None:
    decision = decide(PAN, A=read("ABCPE1234F"), B=read("ABCPE1234F"))
    assert apply_mask(decision, Mask(keep_last=2)).value == "XXXXXXXX4F"
