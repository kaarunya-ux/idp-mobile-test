"""A scanned page rebuilt from OCR's numbered lines and the page model's layout. Made-up page content only."""

from conversion.conversion.domain.document_model import Alignment, Table, TextBlock, TextKind
from conversion.conversion.domain.page_layout import LaidOutTable, LaidOutText, OcrLine, laid_out_page, ocr_page

LINES = [
    OcrLine(number=1, text="TEST LETTERHEAD", top=0.05, block=0),
    OcrLine(number=2, text="A made-up first line", top=0.15, block=1),
    OcrLine(number=3, text="and its second line.", top=0.18, block=1),
    OcrLine(number=4, text="Item", top=0.30, block=2),
    OcrLine(number=5, text="Amount", top=0.30, block=2),
    OcrLine(number=6, text="Test A", top=0.33, block=2),
    OcrLine(number=7, text="12.50", top=0.33, block=2),
    OcrLine(number=8, text="Page 1", top=0.95, block=3),
]


def test_builds_the_page_from_ocrs_text_in_the_models_blocks_when_the_layout_names_every_line() -> None:
    layout = [
        LaidOutText(kind=TextKind.HEADING, lines=(1,), alignment=Alignment.CENTER, bold=True),
        LaidOutText(kind=TextKind.PARAGRAPH, lines=(2, 3)),
        LaidOutTable(rows=(((4,), (5,)), ((6,), (7,))), header_rows=1),
        LaidOutText(kind=TextKind.CAPTION, lines=(8,)),
    ]

    page = laid_out_page(LINES, layout)

    assert page.blocks == (
        TextBlock(text="TEST LETTERHEAD", kind=TextKind.HEADING, alignment=Alignment.CENTER, bold=True),
        TextBlock(text="A made-up first line and its second line."),
        Table(rows=(("Item", "Amount"), ("Test A", "12.50")), header_rows=1),
        TextBlock(text="Page 1", kind=TextKind.CAPTION),
    )


def test_keeps_a_line_the_model_left_out_as_a_paragraph_where_it_sits_when_the_layout_misses_it() -> None:
    layout = [
        LaidOutText(kind=TextKind.HEADING, lines=(1,)),
        LaidOutTable(rows=(((4,), (5,)), ((6,), (7,)))),
    ]

    page = laid_out_page(LINES, layout)

    assert [block.text for block in page.blocks if isinstance(block, TextBlock)] == [
        "TEST LETTERHEAD",
        "A made-up first line and its second line.",
        "Page 1",
    ]
    assert isinstance(page.blocks[2], Table)


def test_uses_each_line_once_and_ignores_numbers_ocr_never_gave_when_the_model_repeats_or_invents_them() -> None:
    layout = [
        LaidOutText(kind=TextKind.PARAGRAPH, lines=(1, 1, 99)),
        LaidOutText(kind=TextKind.PARAGRAPH, lines=(1,)),
    ]

    page = laid_out_page(LINES[:1], layout)

    assert page.blocks == (TextBlock(text="TEST LETTERHEAD"),)


def test_makes_one_list_item_per_line_when_the_block_is_a_list() -> None:
    page = laid_out_page(LINES[1:3], [LaidOutText(kind=TextKind.LIST_ITEM, lines=(2, 3))])

    assert [(block.kind, block.text) for block in page.blocks if isinstance(block, TextBlock)] == [
        (TextKind.LIST_ITEM, "A made-up first line"),
        (TextKind.LIST_ITEM, "and its second line."),
    ]


def test_pads_short_rows_and_drops_a_row_whose_lines_went_elsewhere_when_the_table_is_ragged() -> None:
    layout = [
        LaidOutText(kind=TextKind.PARAGRAPH, lines=(6, 7)),
        LaidOutTable(rows=(((4,), (5,)), ((6,), (7,)), ((8,),)), header_rows=1),
    ]

    page = laid_out_page(LINES[3:], layout)

    assert page.blocks[1] == Table(rows=(("Item", "Amount"), ("Page 1", "")), header_rows=1)


def test_reads_each_ocr_block_as_a_paragraph_when_there_is_no_layout() -> None:
    page = ocr_page(LINES)

    assert [block.text for block in page.blocks if isinstance(block, TextBlock)] == [
        "TEST LETTERHEAD",
        "A made-up first line and its second line.",
        "Item Amount Test A 12.50",
        "Page 1",
    ]
