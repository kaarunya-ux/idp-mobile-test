"""Options are checked as business rules; bad values become CNV-422-OPTIONS-001 (DOM-02, TST-08)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conversion.conversion.domain.exceptions import InvalidOptionError, PageRangeOutsideDocumentError
from conversion.conversion.domain.options import MAX_PAGE_RANGE_LENGTH, ConversionOptions, PageRange


@pytest.mark.parametrize(
    ("text", "page_count", "expected"),
    [
        ("1-3,5", 10, [0, 1, 2, 4]),
        ("5,1-2", 10, [4, 0, 1]),  # request order is kept
        ("2-4,3-5", 10, [1, 2, 3, 4]),  # overlaps count once
        ("3-9", 5, [2, 3, 4]),  # pages past the end are dropped
        (" 1 , 2 ", 5, [0, 1]),  # spaces are ignored
    ],
)
def test_selects_existing_pages_in_request_order_when_parsed(text: str, page_count: int, expected: list[int]) -> None:
    assert PageRange.parse(text).select(page_count) == expected


@pytest.mark.parametrize("text", ["", "0", "3-1", "a", "1-2-3", "1,,2", "-4", "123456"])
def test_rejects_with_cnv_422_options_001_when_the_page_range_is_malformed(text: str) -> None:
    with pytest.raises(InvalidOptionError) as caught:
        PageRange.parse(text)

    assert caught.value.code == "CNV-422-OPTIONS-001"
    assert caught.value.option == "pageRange"


def test_rejects_when_the_page_range_is_too_long() -> None:
    with pytest.raises(InvalidOptionError, match="longer than"):
        PageRange.parse("1," * MAX_PAGE_RANGE_LENGTH)


def test_rejects_when_no_requested_page_exists() -> None:
    with pytest.raises(PageRangeOutsideDocumentError, match="3-page document"):
        PageRange.parse("7-9").select(3)


@given(
    spans=st.lists(st.tuples(st.integers(1, 500), st.integers(0, 50)), min_size=1, max_size=20),
    page_count=st.integers(1, 600),
)
def test_selection_stays_inside_the_document_and_has_no_repeats_for_any_valid_range(
    spans: list[tuple[int, int]], page_count: int
) -> None:
    text = ",".join(f"{first}-{first + extra}" for first, extra in spans)
    try:
        pages = PageRange.parse(text).select(page_count)
    except PageRangeOutsideDocumentError:
        assert all(first > page_count for first, _ in spans)
        return

    assert pages
    assert all(0 <= page < page_count for page in pages)
    assert len(pages) == len(set(pages))


@pytest.mark.parametrize(("dpi", "quality"), [(71, 90), (601, 90), (150, 0), (150, 96)])
def test_rejects_when_dpi_or_quality_is_out_of_range(dpi: int, quality: int) -> None:
    with pytest.raises(InvalidOptionError):
        ConversionOptions(dpi=dpi, jpeg_quality=quality)


def test_uses_sensible_defaults_when_no_options_are_given() -> None:
    options = ConversionOptions()

    assert (options.page_range, options.dpi, options.jpeg_quality) == (None, 150, 90)


def test_rejects_a_page_range_object_when_it_has_no_spans() -> None:
    with pytest.raises(InvalidOptionError, match="at least one page"):
        PageRange(())
