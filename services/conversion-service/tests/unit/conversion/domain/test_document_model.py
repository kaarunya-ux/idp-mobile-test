"""The document model can only hold well-formed pages, text and tables (DOM-02)."""

import pytest

from conversion.conversion.domain.document_model import DocumentModel, Page, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import InvalidDocumentModelError


@pytest.mark.parametrize("text", ["", "   "])
def test_refuses_a_text_block_when_it_has_no_text(text: str) -> None:
    with pytest.raises(InvalidDocumentModelError, match="needs text"):
        TextBlock(text=text)


@pytest.mark.parametrize("level", [0, 7])
def test_refuses_a_text_block_when_its_level_is_out_of_range(level: int) -> None:
    with pytest.raises(InvalidDocumentModelError, match="level"):
        TextBlock(text="Title", kind=TextKind.HEADING, level=level)


@pytest.mark.parametrize(
    ("rows", "header_rows", "message"),
    [
        ((), 0, "at least one cell"),
        (((),), 0, "at least one cell"),
        ((("a", "b"), ("c",)), 0, "same number"),
        ((("a",),), 2, "header rows"),
    ],
)
def test_refuses_a_table_when_it_is_empty_ragged_or_has_too_many_header_rows(
    rows: tuple[tuple[str, ...], ...], header_rows: int, message: str
) -> None:
    with pytest.raises(InvalidDocumentModelError, match=message):
        Table(rows=rows, header_rows=header_rows)


def test_pads_short_rows_when_built_from_a_ragged_grid() -> None:
    table = Table.from_ragged([["item", "qty", "price"], ["Paper", "10"]], header_rows=1)

    assert table.rows == (("item", "qty", "price"), ("Paper", "10", ""))
    assert table.column_count == 3
    assert table.body == (("Paper", "10", ""),)


def test_lists_tables_in_reading_order_when_asked() -> None:
    first, second = Table(rows=(("a",),)), Table(rows=(("b",),))
    model = DocumentModel(pages=(Page(blocks=(TextBlock(text="Intro"), first)), Page(blocks=(second,), name="Sheet 2")))

    assert model.tables == [first, second]
    assert model.pages[0].tables == [first]
