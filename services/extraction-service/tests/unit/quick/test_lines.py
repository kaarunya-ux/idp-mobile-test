"""The generic read over OCR's lines: lines numbered, values filled from the lines the model named. Values invented."""

import pytest

from extraction.quick.domain.lines import LinesField, LinesReading, number_lines, result_from_lines
from extraction.quick.domain.models import ReadBy, Table
from idp_ocr_engines.model import Box, OcrPage, OcrWord


def _word(text: str, x: float, y: float, line: int, confidence: float = 0.9) -> OcrWord:
    box = Box(x=x, y=y, width=0.1, height=0.04)
    return OcrWord(text=text, box=box, confidence=confidence, line_id=line, block_id=0)


FRONT = OcrPage(
    width=1000,
    height=600,
    words=(
        _word("MOCK", 0.1, 0.05, 0),
        _word("CARD", 0.21, 0.05, 0),
        _word("No:", 0.1, 0.2, 1),
        _word("TS0100", 0.25, 0.2, 1, 0.8),
        _word("Email:demo@mock.example", 0.1, 0.3, 2),
        _word("Name", 0.1, 0.4, 3),
        _word("DEMO", 0.1, 0.5, 4),
        _word("PERSON", 0.21, 0.5, 4),
    ),
)
BACK = OcrPage(
    width=1000,
    height=600,
    words=(
        _word("10,", 0.1, 0.1, 0),
        _word("MOCK", 0.21, 0.1, 0),
        _word("ROAD", 0.32, 0.1, 0),
        _word("TESTPUR", 0.1, 0.15, 1),
        _word("Item", 0.1, 0.5, 2),
        _word("Qty", 0.5, 0.5, 3),
        _word("Pens", 0.1, 0.6, 4),
        _word("5", 0.5, 0.6, 5),
        _word("Blood", 0.1, 0.7, 6),
        _word("Group:", 0.21, 0.7, 6),
        _word("O-", 0.32, 0.7, 6),
    ),
)
LINES = number_lines([FRONT, BACK])


def _read(*fields: LinesField) -> list[tuple[str, object]]:
    reading = LinesReading(
        document_type="mock_card", document_confidence=0.9, fields=fields, model_id="m", prompt_version="v"
    )
    result = result_from_lines(reading, LINES)
    assert result.read_by is ReadBy.AI
    return [(f.key, f.value) for f in result.fields]


def test_numbers_every_line_across_the_pages_in_upload_order() -> None:
    assert [(line.number, line.page, line.text) for line in LINES[:2]] == [(0, 1, "MOCK CARD"), (1, 1, "No: TS0100")]
    assert (LINES[5].page, LINES[5].text) == (2, "10, MOCK ROAD")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (LinesField(key="number", label="No", type="string", lines=(1,)), "TS0100"),  # the label's word left out
        (LinesField(key="email", label="Email", type="string", lines=(2,)), "demo@mock.example"),  # joined to it
        (LinesField(key="name", label="Name", type="string", lines=(4,)), "DEMO PERSON"),  # label on its own line
        (LinesField(key="address", label="Address", type="string", lines=(5, 6)), "10, MOCK ROAD TESTPUR"),
        (LinesField(key="number", label="TS0100", type="string", lines=(1,)), "No: TS0100"),  # value given as label
        (LinesField(key="phone", label="Phone", type="string", lines=(99,)), None),  # not a line: ignored
        (LinesField(key="blood_group", label="Blood Group", type="string", lines=(11,)), "O-"),  # its sign kept
    ],
)
def test_fills_each_value_with_ocr_text_from_its_lines(field: LinesField, value: str | None) -> None:
    assert _read(field) == [(field.key, value)]


def test_boxes_a_value_on_its_own_words_on_its_page() -> None:
    reading = LinesReading(
        document_type="mock_card",
        document_confidence=0.9,
        fields=[LinesField(key="number", label="No", type="string", lines=(1,))],
        model_id="m",
        prompt_version="v",
    )

    (field,) = result_from_lines(reading, LINES).fields

    assert field.box is not None
    assert (field.box.page, field.box.box.x, field.box.box.y) == (1, pytest.approx(0.25), pytest.approx(0.2))
    assert field.confidence == pytest.approx(0.8)  # the value's word, not the label's


def test_reads_a_table_as_headers_and_rows_without_a_box() -> None:
    table = LinesField(key="items", label="Items", type="table", rows=(((7,), (8,)), ((9,), (10,))))

    assert _read(table) == [("items", Table(headers=["Item", "Qty"], rows=[["Pens", "5"]]))]
