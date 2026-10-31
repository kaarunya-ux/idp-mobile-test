"""The document model is plain, immutable data (DOM-01)."""

from idox_conversion.conversion.domain.document_model import Page, TableBlock, TextBlock, TextKind


def test_page_lists_its_tables_in_reading_order() -> None:
    first = TableBlock(header=("A",), rows=(("1",),), columns=1)
    second = TableBlock(header=("B",), rows=(("2",),), columns=1)
    page = Page(
        blocks=(
            TextBlock(kind=TextKind.HEADING, text="Title"),
            first,
            TextBlock(kind=TextKind.PARAGRAPH, text="x"),
            second,
        )
    )

    assert page.tables == (first, second)
