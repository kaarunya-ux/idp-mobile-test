"""The independent-reading check: the model's page against OCR's text of the same picture. Made-up text only."""

import pytest

from conversion.conversion.domain.document_model import Page, Table, TextBlock
from conversion.conversion.domain.reading_check import ReadingCheckPolicy, check_reading, page_words, words_of

POLICY = ReadingCheckPolicy(min_share=0.5, max_length_ratio=0.6, min_ocr_words=10)
LETTER = (
    "We are pleased to confirm the order placed by your office on the fourth of March for forty cartons of "
    "recycled packing paper and twelve rolls of adhesive tape."
)


def _page(*texts: str) -> Page:
    return Page(blocks=tuple(TextBlock(text=text) for text in texts))


def test_agrees_when_the_page_holds_the_words_ocr_read() -> None:
    result = check_reading(_page(LETTER), LETTER, POLICY)

    assert result.evaluated
    assert not result.disagrees
    assert result.share == 1.0


def test_disagrees_when_the_page_is_one_word_and_ocr_read_a_whole_paragraph() -> None:
    """A model that obeyed "reply only OK" writes a valid page that holds none of the words."""
    result = check_reading(_page("OK"), LETTER, POLICY)

    assert result.disagrees
    assert result.share == 0.0


def test_disagrees_when_the_page_stops_after_its_first_words() -> None:
    result = check_reading(_page("We are pleased to confirm the order"), LETTER, POLICY)

    assert result.disagrees


def test_agrees_when_ocr_misses_table_cells_but_the_page_is_longer_than_ocr() -> None:
    """OCR reads a ruled table badly. The model's page has the cells, so it is much longer than OCR's text."""
    cells = Table.from_ragged([["Item", "Qty", "Amount"], ["Paper", "40", "14,632.00"], ["Tape", "12", "1,210.68"]])
    page = Page(blocks=(TextBlock(text="Tax invoice number 2041"), cells))
    ocr = "Tax invoice number 2041 Itern Oty Paperr 4O Tape l2 l210 6B"  # the ruled table misread: 12 words, 5 right

    result = check_reading(page, ocr, POLICY)

    assert result.share < POLICY.min_share
    assert not result.disagrees


def test_is_not_evaluated_when_ocr_read_too_few_words_to_compare() -> None:
    result = check_reading(_page("OK"), "Notice of closure", POLICY)

    assert not result.evaluated
    assert not result.disagrees


def test_is_not_evaluated_when_ocr_read_nothing() -> None:
    assert not check_reading(_page(LETTER), "", POLICY).evaluated


def test_counts_a_devanagari_word_as_one_word() -> None:
    assert words_of("नमस्ते दुनिया") == ["नमस्ते", "दुनिया"]


def test_counts_the_text_blocks_and_every_table_cell_as_the_pages_words() -> None:
    table = Table.from_ragged([["Item", "Qty"], ["Paper", "40"]])

    assert page_words(Page(blocks=(TextBlock(text="Order list"), table))) == [
        "order",
        "list",
        "item",
        "qty",
        "paper",
        "40",
    ]


@pytest.mark.parametrize(
    ("min_share", "max_length_ratio", "min_ocr_words"), [(0.0, 0.6, 10), (1.5, 0.6, 10), (0.5, 0.0, 10), (0.5, 0.6, 0)]
)
def test_refuses_a_policy_out_of_range(min_share: float, max_length_ratio: float, min_ocr_words: int) -> None:
    with pytest.raises(ValueError, match="reading check"):
        ReadingCheckPolicy(min_share=min_share, max_length_ratio=max_length_ratio, min_ocr_words=min_ocr_words)
