"""A custom template from a user's keys: labels found where they open a piece of text, values read after them."""

import re
from datetime import date

import pytest

from extraction.pipeline.domain.custom_template import (
    CUSTOM_SCOPE,
    CustomField,
    CustomType,
    custom_template,
    label_pattern,
)
from extraction.pipeline.domain.reading import read_field
from extraction.pipeline.domain.template import FieldType
from idp_ocr_engines.model import Box, OcrWord

TODAY = date(2026, 11, 4)


def word(text: str, x: float, y: float, width: float = 0.2) -> OcrWord:
    return OcrWord(text=text, box=Box(x=x, y=y, width=width, height=0.04), confidence=0.9, line_id=0, block_id=0)


@pytest.mark.parametrize(
    ("label", "text", "found"),
    [
        ("Name", "Name", True),
        ("Name", "Father's Name", False),  # a label opens its piece: not the end of another one
        ("Department", "INCOME TAX DEPARTMENT", False),
        ("Father's Name", "Father" + chr(0x2019) + "s Name:", True),  # a typographic apostrophe
        ("Date of Birth", "DATE OF BIRTH: 01/01/1990", True),
        ("Card No", "Card No.: 123", True),
        ("Card No", "Card Number", False),
    ],
)
def test_finds_a_label_only_where_it_opens_a_piece_of_text(label: str, text: str, found: bool) -> None:
    assert bool(re.search(label_pattern(label), text)) is found


def test_builds_one_required_field_per_key_in_order_with_its_type() -> None:
    template = custom_template(
        [
            CustomField(key="card_number", label="Card No"),
            CustomField(key="expiry", label="Expires", type=CustomType.DATE),
        ]
    )

    assert template.scope == CUSTOM_SCOPE
    assert [(f.key, f.type, f.required) for f in template.fields] == [
        ("card_number", FieldType.TEXT, True),
        ("expiry", FieldType.DATE, True),
    ]


def _field(label: str, type_: CustomType = CustomType.TEXT):  # type: ignore[no-untyped-def]  # a FieldDefinition
    return custom_template([CustomField(key="k", label=label, type=type_)]).fields[0]


def test_reads_the_value_after_the_label_in_the_same_piece() -> None:
    reading = read_field(
        _field("Card No"), [word("Card No: SRC1234567", 0.1, 0.2)], Box(x=0, y=0, width=1, height=1), today=TODAY
    )

    assert reading is not None
    assert reading.text == "SRC1234567"


def test_reads_the_value_in_the_piece_right_of_the_label() -> None:
    words = [word("Card No", 0.1, 0.2), word("SRC1234567", 0.4, 0.2), word("Other", 0.1, 0.4)]

    reading = read_field(_field("Card No"), words, Box(x=0, y=0, width=1, height=1), today=TODAY)

    assert reading is not None
    assert reading.text == "SRC1234567"


def test_reads_the_line_below_a_label_that_stands_alone() -> None:
    words = [word("Name", 0.1, 0.2), word("TEST PERSON", 0.1, 0.27), word("Father's Name", 0.1, 0.34)]

    reading = read_field(_field("Name"), words, Box(x=0, y=0, width=1, height=1), today=TODAY)

    assert reading is not None
    assert reading.text == "TEST PERSON"


def test_reads_nothing_when_the_label_is_not_on_the_page() -> None:
    words = [word("INCOME TAX DEPARTMENT", 0.1, 0.1), word("TEST PERSON", 0.1, 0.2)]

    assert read_field(_field("Department"), words, Box(x=0, y=0, width=1, height=1), today=TODAY) is None


def test_cuts_a_date_out_of_the_text_after_its_label_and_accepts_a_future_expiry() -> None:
    reading = read_field(
        _field("Expires", CustomType.DATE),
        [word("Expires: 01/12/2031 (renew)", 0.1, 0.2)],
        Box(x=0, y=0, width=1, height=1),
        today=TODAY,
    )

    assert reading is not None
    assert reading.text == "01/12/2031"
