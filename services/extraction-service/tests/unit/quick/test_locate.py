"""Placing the generic read's values on the page from the OCR words. All values are invented."""

import pytest

from extraction.quick.domain.locate import locate, place_fields
from extraction.quick.domain.models import ExtractedField, Table
from extraction.quick.domain.placed import PlacedBox
from idp_ocr_engines.model import Box, OcrPage, OcrWord

_H = 0.04


def _word(text: str, x: float, y: float, width: float = 0.1) -> OcrWord:
    return OcrWord(text=text, box=Box(x=x, y=y, width=width, height=_H), confidence=0.9, line_id=0, block_id=0)


def _page(*words: OcrWord) -> OcrPage:
    return OcrPage(width=1000, height=800, words=words)


INVOICE = _page(
    _word("Invoice", 0.05, 0.10),
    _word("Date", 0.16, 0.10),
    _word("23-Jul-2025", 0.30, 0.10, 0.15),
    _word("Challan", 0.05, 0.20),
    _word("Date", 0.16, 0.20),
    _word("23-Jul-2025", 0.30, 0.20, 0.15),
    _word("Customer:", 0.05, 0.30),
    _word("Demo", 0.30, 0.30),
    _word("Traders", 0.41, 0.30),
    _word("Pvt", 0.52, 0.30),
    _word("Sex", 0.05, 0.40),
    _word("F", 0.30, 0.40, 0.02),
    _word("Phone:9876500000", 0.05, 0.50, 0.3),
)


def _spot(found: PlacedBox | None) -> tuple[int, float, float, float]:
    assert found is not None
    return found.page, round(found.box.x, 4), round(found.box.y, 4), round(found.box.width, 4)


def test_places_a_value_on_the_words_that_spell_it_across_several_words() -> None:
    assert _spot(locate("Demo Traders Pvt", "Customer", [INVOICE])) == (1, 0.30, 0.30, 0.32)


def test_places_a_value_printed_twice_next_to_its_own_label() -> None:
    assert _spot(locate("23-Jul-2025", "Challan Date", [INVOICE]))[2] == pytest.approx(0.20)
    assert _spot(locate("23-Jul-2025", "Invoice Date", [INVOICE]))[2] == pytest.approx(0.10)


def test_places_a_short_value_only_when_its_label_is_printed_before_it() -> None:
    assert _spot(locate("F", "Sex", [INVOICE]))[1:3] == (0.30, 0.40)
    assert locate("F", "Gender", [INVOICE]) is None


def test_places_a_value_ocr_joined_to_its_label_on_that_word() -> None:
    assert _spot(locate("98765 00000", "Phone", [INVOICE])) == (1, 0.05, 0.50, 0.3)


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("Not On The Page", "Remarks"),
        ("23-Jul-2025", "Due Date"),  # printed twice and its label isn't: can't tell which
        ("", "Empty"),
    ],
)
def test_gives_no_box_when_the_value_cant_be_placed_for_sure(value: str, label: str) -> None:
    assert locate(value, label, [INVOICE]) is None


def test_counts_pages_from_one_in_upload_order() -> None:
    back = _page(_word("Blood", 0.1, 0.1), _word("Group", 0.21, 0.1), _word("B+", 0.4, 0.1, 0.05))

    assert _spot(locate("B+", "Blood Group", [INVOICE, back]))[0] == 2


def test_gives_text_fields_a_box_and_leaves_tables_without_one() -> None:
    fields = [
        ExtractedField(key="customer", label="Customer", type="string", value="Demo Traders Pvt", confidence=0.9),
        ExtractedField(key="items", label="Items", type="table", value=Table(headers=["A"], rows=[]), confidence=0.8),
        ExtractedField(key="note", label="Note", type="string", value=None, confidence=None),
    ]

    placed = place_fields(fields, [INVOICE])

    assert [field.box is not None for field in placed] == [True, False, False]
