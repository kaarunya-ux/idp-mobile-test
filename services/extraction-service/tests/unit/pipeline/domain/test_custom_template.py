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
        ("USCIS#", "USCIS#", True),
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


@pytest.mark.parametrize("printed", ["20 OCT 2002", "10/26/32", "October 20, 2002", "2002-10-20"])
def test_reads_a_date_as_printed_whether_the_day_or_the_month_comes_first(printed: str) -> None:
    reading = read_field(
        _field("Date of Birth", CustomType.DATE),
        [word("Date of Birth", 0.4, 0.5), word(printed, 0.4, 0.56)],
        Box(x=0, y=0, width=1, height=1),
        today=TODAY,
    )

    assert reading is not None
    assert reading.text == printed


def test_reads_the_value_and_not_the_end_of_a_label_typed_with_a_hash() -> None:
    reading = read_field(
        _field("USCIS#"),
        [word("USCIS#", 0.4, 0.4), word("000-000-001", 0.4, 0.46)],
        Box(x=0, y=0, width=1, height=1),
        today=TODAY,
    )

    assert reading is not None
    assert reading.text == "000-000-001"


def test_stops_the_value_after_a_label_at_the_next_columns_text() -> None:
    words = [word("GSTIN: 32AAAAA0000A1ZA", 0.05, 0.3, width=0.3), word("Transport ID 24BBBBB1111B2ZB", 0.55, 0.3)]

    reading = read_field(_field("GSTIN"), words, Box(x=0, y=0, width=1, height=1), today=TODAY)

    assert reading is not None
    assert reading.text == "32AAAAA0000A1ZA"


def test_stops_a_value_right_of_its_label_at_the_next_column() -> None:
    words = [word("M/S", 0.05, 0.3, width=0.04), word("Demo Test Works", 0.15, 0.3), word("Challan No 33", 0.6, 0.3)]

    reading = read_field(_field("M/S"), words, Box(x=0, y=0, width=1, height=1), today=TODAY)

    assert reading is not None
    assert reading.text == "Demo Test Works"


def _drawn(label: str, box: Box):  # type: ignore[no-untyped-def]  # a FieldDefinition
    return custom_template([CustomField(key="k", label=label, box=box)]).fields[0]


def test_reads_only_the_words_inside_a_box_drawn_around_the_value() -> None:
    # one piece holds the label, the value and the next column's text, as an engine may read a row
    words = [word("M/S Demo Test Works Challan No 33", 0.05, 0.3, width=0.66)]
    definition = _drawn("M/S", Box(x=0.11, y=0.3, width=0.32, height=0.04))  # "Demo Test Works"

    reading = read_field(definition, words, definition.box, today=TODAY)

    assert reading is not None
    assert reading.text == "Demo Test Works"


@pytest.mark.parametrize(
    ("text", "value"), [("Transport ID:24ABCDE0000F1ZG", "24ABCDE0000F1ZG"), ("At:10:30", "10:30")]
)
def test_cuts_a_label_run_into_its_value_without_a_space_at_the_drawn_edge(text: str, value: str) -> None:
    piece = word(text, 0.5, 0.3, width=0.04 * len(text) / 4)
    step = piece.box.width / len(text)
    start = piece.box.x + step * (len(text) - len(value))
    definition = _drawn("Transport ID", Box(x=start + 0.006, y=0.3, width=step * len(value) - 0.012, height=0.04))

    reading = read_field(definition, [piece], definition.box, today=TODAY)

    assert reading is not None
    assert reading.text == value


def test_reads_every_line_inside_a_drawn_box() -> None:
    words = [word("12 Test Road", 0.1, 0.3), word("Test City", 0.1, 0.35), word("Phone 000", 0.1, 0.5)]
    definition = _drawn("Address", Box(x=0.1, y=0.3, width=0.3, height=0.09))

    reading = read_field(definition, words, definition.box, today=TODAY)

    assert reading is not None
    assert reading.text == "12 Test Road Test City"


def test_reads_after_the_label_when_the_drawn_box_holds_nothing() -> None:
    words = [word("Card No: SRC1234567", 0.1, 0.6, width=0.3)]  # printed lower than on the sample
    definition = _drawn("Card No", Box(x=0.2, y=0.2, width=0.2, height=0.04))

    reading = read_field(definition, words, definition.box, today=TODAY)

    assert reading is not None
    assert reading.text == "SRC1234567"


def test_widens_a_drawn_box_a_little_and_keeps_it_inside_the_page() -> None:
    definition = _drawn("Total", Box(x=0.0, y=0.5, width=0.3, height=0.02))

    assert definition.clip_to_box
    assert (definition.box.x, definition.box.y) == (0.0, 0.49)
    assert definition.box.width == pytest.approx(0.305)
    assert definition.box.height == pytest.approx(0.04)
