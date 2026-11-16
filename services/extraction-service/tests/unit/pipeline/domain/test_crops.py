"""A field's crop for the AI step: the box with a margin, kept on the page, cut from the RGB bytes."""

from extraction.pipeline.domain.crops import crop_page, padded
from idp_ocr_engines.model import Box, PageImage


def _page(width: int, height: int) -> PageImage:
    """Each pixel's red value is its column and its green value its row, so a crop shows where it was cut."""
    rgb = bytes(value for y in range(height) for x in range(width) for value in (x, y, 0))
    return PageImage(width=width, height=height, rgb=rgb)


def test_grows_the_box_by_half_its_height_on_every_side() -> None:
    grown = padded(Box(x=0.4, y=0.4, width=0.2, height=0.1), padding=0.5)

    assert (round(grown.x, 4), round(grown.y, 4), round(grown.width, 4), round(grown.height, 4)) == (
        0.35,
        0.35,
        0.3,
        0.2,
    )


def test_keeps_the_padded_box_inside_the_page_at_an_edge() -> None:
    grown = padded(Box(x=0.0, y=0.9, width=0.5, height=0.1), padding=1.0)

    assert (grown.x, grown.y + grown.height) == (0.0, 1.0)


def test_cuts_the_pixels_inside_the_box() -> None:
    crop = crop_page(_page(10, 8), Box(x=0.2, y=0.25, width=0.3, height=0.5))

    assert (crop.width, crop.height) == (3, 4)
    assert crop.rgb[:3] == bytes((2, 2, 0))  # the top-left pixel is column 2, row 2
    assert crop.rgb[-3:] == bytes((4, 5, 0))
