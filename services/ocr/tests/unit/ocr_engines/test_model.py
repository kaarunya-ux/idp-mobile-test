"""The data model: boxes in page fractions, page images, words and regions cannot be built in an invalid state."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from idp_ocr_engines.errors import InvalidGeometryError
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, OcrWord, PageImage

BOX = Box(0.1, 0.2, 0.3, 0.4)


def _word(text: str, line_id: int) -> OcrWord:
    return OcrWord(text=text, box=BOX, confidence=0.9, line_id=line_id, block_id=0)


@pytest.mark.parametrize(
    ("x", "y", "width", "height"),
    [
        (-0.1, 0, 0.5, 0.5),
        (0, -0.1, 0.5, 0.5),
        (0, 0, 0, 0.5),
        (0, 0, 0.5, 0),
        (0.6, 0, 0.5, 0.5),
        (0, 0.6, 0.5, 0.5),
        (0, 0, 1.5, 1),
    ],
)
def test_refuses_to_build_a_box_when_it_is_empty_or_outside_the_page(
    x: float, y: float, width: float, height: float
) -> None:
    with pytest.raises(InvalidGeometryError):
        Box(x, y, width, height)


def test_accepts_a_box_when_it_covers_the_whole_page() -> None:
    assert Box(0, 0, 1, 1).to_pixels(page_width=200, page_height=100) == (0, 0, 200, 100)


def test_clips_the_box_to_the_page_when_pixel_corners_stick_out() -> None:
    box = Box.from_pixels(-10, -5, 120, 80, page_width=100, page_height=50)

    assert box == Box(0, 0, 1, 1)


@pytest.mark.parametrize("corners", [(50, 10, 50, 20), (60, 10, 50, 20), (10, 30, 20, 30), (200, 0, 300, 10)])
def test_refuses_to_build_a_box_when_no_area_is_left_after_clipping(corners: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = corners

    with pytest.raises(InvalidGeometryError):
        Box.from_pixels(left, top, right, bottom, page_width=100, page_height=50)


@given(st.integers(0, 799), st.integers(0, 599), st.integers(1, 400), st.integers(1, 300))
def test_returns_the_same_pixels_within_one_pixel_when_a_box_is_converted_both_ways(
    left: int, top: int, width: int, height: int
) -> None:
    right, bottom = min(left + width, 800), min(top + height, 600)
    box = Box.from_pixels(left, top, right, bottom, page_width=800, page_height=600)

    back = box.to_pixels(page_width=800, page_height=600)

    assert all(abs(a - b) <= 1 for a, b in zip(back, (left, top, right, bottom), strict=True))


def test_accepts_a_page_image_when_the_byte_count_matches() -> None:
    image = PageImage(width=2, height=3, rgb=bytes(18))

    assert (image.pixel_count, image.page_index) == (6, 0)


@pytest.mark.parametrize(("width", "height", "size"), [(2, 3, 17), (2, 3, 19), (0, 3, 0), (2, 0, 0), (-1, 3, 0)])
def test_refuses_to_build_a_page_image_when_dimensions_or_bytes_are_wrong(width: int, height: int, size: int) -> None:
    with pytest.raises(InvalidGeometryError):
        PageImage(width=width, height=height, rgb=bytes(size))


@pytest.mark.parametrize(("confidence", "line_id", "block_id"), [(-0.1, 0, 0), (1.1, 0, 0), (0.5, -1, 0), (0.5, 0, -1)])
def test_refuses_to_build_a_word_when_confidence_or_ids_are_out_of_range(
    confidence: float, line_id: int, block_id: int
) -> None:
    with pytest.raises(InvalidGeometryError):
        OcrWord(text="x", box=BOX, confidence=confidence, line_id=line_id, block_id=block_id)


def test_refuses_to_build_a_region_when_confidence_is_out_of_range() -> None:
    with pytest.raises(InvalidGeometryError):
        OcrRegion(box=BOX, text="x", confidence=2.0)


def test_joins_words_line_by_line_when_the_text_of_a_page_is_read() -> None:
    page = OcrPage(width=10, height=10, words=(_word("Name", 0), _word("ROSHAN", 1), _word("SAHANI", 1)))

    assert page.text == "Name\nROSHAN SAHANI"
