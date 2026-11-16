"""Which template a document is, ours or the user's: keywords (a user's template: its labels) first, the AI's pick
otherwise, 85% either way. All text is invented."""

import re

import pytest

from extraction.pipeline.domain.custom_template import CustomField
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.quick.domain.detection import (
    AI_CANDIDATES,
    DetectedBy,
    UserTemplate,
    by_code,
    by_keywords,
    label_keyword,
    ours,
    read_enough,
    scored,
    shortlist,
    type_code,
    users,
)
from extraction.quick.domain.models import ExtractedField, QuickResult, ReadBy
from idp_ocr_engines.model import Box, OcrPage, OcrWord

MINIMUM = 0.85
OURS = [ours(t) for t in STANDARD_TEMPLATES]
GREEN_CARD = users(
    UserTemplate(
        id="backend-7",
        name="Green card",
        fields=(
            CustomField(key="surname", label="Surname"),
            CustomField(key="uscis_number", label="USCIS#"),
            CustomField(key="date_of_birth", label="Date of Birth"),
        ),
        field_ids={"surname": "f1"},
    )
)


def _page(text: str, width: int = 1000, height: int = 630) -> OcrPage:
    words = tuple(
        OcrWord(text=w, box=Box(x=0.01 * i, y=0.1, width=0.01, height=0.05), confidence=0.9, line_id=0, block_id=0)
        for i, w in enumerate(text.split())
    )
    return OcrPage(width=width, height=height, words=words)


PAN = "INCOME TAX DEPARTMENT GOVT. OF INDIA Permanent Account Number Card ABCPE1234F"
GREEN = "PERMANENT RESIDENT Surname MOCK Given Name DEMO USCIS# 000-000-001 Date of Birth 01 JAN 1990"


def test_finds_our_template_by_its_keywords() -> None:
    found = by_keywords(scored([*OURS, GREEN_CARD], [_page(PAN)]), MINIMUM)

    assert found is not None
    assert (found.candidate.template.document_type, found.confidence) == ("PAN_CARD", 1.0)
    assert (found.candidate.own, found.detected_by) == (False, DetectedBy.KEYWORDS)


def test_finds_the_users_template_by_its_labels_whatever_the_page_shape() -> None:
    found = by_keywords(scored([*OURS, GREEN_CARD], [_page(GREEN, width=1000, height=1000)]), MINIMUM)

    assert found is not None
    assert (found.candidate.id, found.candidate.name, found.confidence) == ("backend-7", "Green card", 1.0)
    assert found.candidate.field_ids == {"surname": "f1"}


def test_prefers_the_users_own_template_on_a_tie() -> None:
    copy = users(UserTemplate(id="backend-9", name="My PAN", fields=(CustomField(key="pan", label="INCOME TAX"),)))

    found = by_keywords(scored([*OURS, copy], [_page(PAN)]), MINIMUM)

    assert found is not None
    assert found.candidate.id == "backend-9"


@pytest.mark.parametrize(
    "page",
    [
        _page("INCOME TAX DEPARTMENT"),  # one of PAN's three keywords
        _page(PAN, width=1000, height=1000),  # every keyword, but a square page: halved
        _page("Surname MOCK Date of Birth 01 JAN 1990"),  # two of the green card's three labels
        _page("Demo Traders Invoice 1001"),
        OcrPage(width=1000, height=630, words=()),
    ],
)
def test_finds_nothing_below_the_threshold(page: OcrPage) -> None:
    assert by_keywords(scored([*OURS, GREEN_CARD], [page]), MINIMUM) is None


def test_offers_the_ai_the_best_scoring_candidates_as_unique_codes() -> None:
    twin = users(UserTemplate(id="backend-8", name="Green card", fields=(CustomField(key="a", label="Alien"),)))

    codes = shortlist(scored([*OURS, GREEN_CARD, twin], [_page("Surname MOCK Alien")]))

    assert set(codes) == {"GREEN_CARD", "GREEN_CARD_2"}  # ours show no sign on this page
    assert (codes["GREEN_CARD"].id, codes["GREEN_CARD_2"].id) == ("backend-8", "backend-7")  # its one label: best


def test_offers_the_ai_at_most_its_limit_of_candidates() -> None:
    many = [
        users(UserTemplate(id=f"t{n}", name=f"Form {n}", fields=(CustomField(key="a", label="A"),))) for n in range(80)
    ]

    assert len(shortlist(scored([*OURS, *many], [_page("A form")]))) == AI_CANDIDATES


def test_offers_the_ai_only_templates_with_evidence_on_the_page() -> None:
    jamaican_id = _page("NATIONAL IDENTIFICATION CARD Surname MOCK Date of Birth 01 JAN 1990 Sex F")

    assert shortlist(scored(OURS, [jamaican_id])) == {}  # Aadhaar: 1 keyword of 4, under its minimum of 2


def test_takes_the_template_the_ai_names_when_it_is_sure_enough() -> None:
    codes = shortlist(scored([*OURS, GREEN_CARD], [_page("Surname")]))

    found = by_code(codes, "GREEN_CARD", 0.9, MINIMUM)

    assert found is not None
    assert (found.candidate.id, found.detected_by) == ("backend-7", DetectedBy.AI)


@pytest.mark.parametrize(("code", "confidence"), [("GREEN_CARD", 0.8), (None, 0.99), ("PASSPORT", 0.99)])
def test_takes_no_ai_answer_that_is_unsure_or_not_offered(code: str | None, confidence: float) -> None:
    codes = shortlist(scored([*OURS, GREEN_CARD], [_page("Surname")]))

    assert by_code(codes, code, confidence, MINIMUM) is None


def test_matches_a_label_as_whole_words_with_any_separators() -> None:
    pattern = label_keyword("Date of Birth")

    assert all(
        re.search(pattern, text, re.IGNORECASE) for text in ("DATE OF BIRTH 01", "Date-of-Birth:", "date  of birth")
    )
    assert not re.search(pattern, "Update of Birthday", re.IGNORECASE)


def test_names_a_users_template_type_from_its_name() -> None:
    assert (type_code("Green Card"), type_code("Employee ID (2025)"), type_code("!!")) == (
        "green_card",
        "employee_id_2025",
        "custom",
    )


def test_finds_an_aadhaar_card_that_prints_the_issuer_in_full_and_no_aadhaar_word() -> None:
    printed = (
        "Government of India Unique Identification Authority of India (TEST SAMPLE) Demo Person QR code "
        "DOB: 13/11/1994 photograph Male Address: Block O, 101, Mock Road 5640 2947 6217"
    )

    found = by_keywords(scored(OURS, [_page(printed)]), MINIMUM)

    assert found is not None
    assert (found.candidate.template.document_type, found.confidence) == ("AADHAAR_CARD", 1.0)


def _read(*values: str | None) -> QuickResult:
    fields = [
        ExtractedField(key=f"k{n}", label=f"K{n}", type="string", value=v, confidence=None)
        for n, v in enumerate(values)
    ]
    return QuickResult(document_type="pan_card", document_confidence=0.9, fields=fields, read_by=ReadBy.OCR)


@pytest.mark.parametrize(
    ("result", "kept"),
    [
        (_read("A", "B", None, None), True),  # half: enough
        (_read("A", None, None), False),
        (_read(None, ""), False),  # an empty value is no value
        (_read(), False),
    ],
)
def test_keeps_the_template_only_when_it_read_enough_of_its_fields(result: QuickResult, kept: bool) -> None:
    assert read_enough(result, 0.5) is kept


def test_finds_a_users_template_by_labels_in_hindi() -> None:
    hindi = users(UserTemplate(id="h1", name="परीक्षण पत्र", fields=(CustomField(key="name", label="नाम"),)))

    found = by_keywords(scored([hindi], [_page("नाम परीक्षण व्यक्ति")]), MINIMUM)

    assert found is not None
    assert (found.candidate.id, type_code(hindi.name)) == ("h1", "custom")  # a code is ASCII: "custom" stands in
