"""Stage SCAN and page loading: real file types, caps, EXIF rotation, PDF rendering (FIL-03, FIL-05)."""

import io

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image

from extraction.core.error_codes import ErrorCode
from extraction.pipeline.domain.exceptions import FileRejectedError, TooManyPagesError
from extraction.pipeline.infrastructure.quality.page_loader import DocumentPageLoader
from idp_ocr_engines.engines.arrays import to_rgb_array

LOADER = DocumentPageLoader(max_file_bytes=5_000_000, max_pages=3, max_pixels=4_000_000, pdf_dpi=300)


def _encoded(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def _pdf(pages: int, size: tuple[float, float] = (595, 842)) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(*size)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("fmt", "options"), [("PNG", {}), ("JPEG", {}), ("WEBP", {"lossless": True}), ("BMP", {}), ("TIFF", {})]
)
def test_loads_one_page_when_the_file_is_a_supported_image(fmt: str, options: dict[str, object]) -> None:
    pages = LOADER.load_pages(_encoded(Image.new("RGB", (60, 40), (255, 0, 0)), fmt, **options))

    assert [(page.width, page.height, page.page_index) for page in pages] == [(60, 40, 0)]


def test_keeps_the_colour_order_when_a_red_image_is_loaded() -> None:
    page = LOADER.load_pages(_encoded(Image.new("RGB", (8, 8), (255, 0, 0)), "PNG"))[0]

    assert to_rgb_array(page)[0, 0].tolist() == [255, 0, 0]


def test_turns_a_sideways_photo_upright_when_the_exif_orientation_says_so() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # the camera stored it sideways; show it rotated 90 degrees clockwise
    photo = Image.new("RGB", (80, 40), (0, 0, 255))

    page = LOADER.load_pages(_encoded(photo, "JPEG", exif=exif))[0]

    assert (page.width, page.height) == (40, 80)


def test_loads_every_page_when_the_tiff_has_several() -> None:
    frames = [Image.new("RGB", (30, 20), (index * 40, 0, 0)) for index in range(3)]
    data = _encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:])

    assert [page.page_index for page in LOADER.load_pages(data)] == [0, 1, 2]


def test_renders_a_pdf_page_at_300_dpi_when_the_file_is_an_a4_pdf() -> None:
    loader = DocumentPageLoader(max_file_bytes=5_000_000, max_pages=3, max_pixels=20_000_000, pdf_dpi=300)

    page = loader.load_pages(_pdf(1))[0]

    assert (page.width, page.height) == pytest.approx((2480, 3508), abs=2)


def test_scales_a_huge_pdf_page_down_when_it_would_pass_the_pixel_cap() -> None:
    page = LOADER.load_pages(_pdf(1))[0]

    assert page.pixel_count <= 4_000_000


def test_loads_every_page_when_the_pdf_has_several() -> None:
    assert [page.page_index for page in LOADER.load_pages(_pdf(3))] == [0, 1, 2]


@pytest.mark.parametrize("content", [b"", b"plain text, not a document", b"GIF89a" + bytes(40), b"%PDF-1.4 truncated"])
def test_rejects_the_file_when_it_is_empty_unsupported_or_damaged(content: bytes) -> None:
    with pytest.raises(FileRejectedError) as caught:
        LOADER.load_pages(content)

    assert caught.value.code is ErrorCode.FILE_REJECTED


def test_rejects_the_file_when_it_is_larger_than_the_cap() -> None:
    small = DocumentPageLoader(max_file_bytes=100, max_pages=3, max_pixels=4_000_000, pdf_dpi=300)

    with pytest.raises(FileRejectedError):
        small.load_pages(_encoded(Image.new("RGB", (200, 200), (1, 2, 3)), "PNG"))


def test_rejects_the_image_when_its_header_declares_more_pixels_than_the_cap() -> None:
    bomb = _encoded(Image.new("1", (3000, 3000)), "PNG")  # a few hundred bytes that would decode to 9 million pixels

    with pytest.raises(FileRejectedError, match="more pixels"):
        LOADER.load_pages(bomb)


def test_rejects_the_file_when_it_has_more_pages_than_the_cap() -> None:
    with pytest.raises(TooManyPagesError) as caught:
        LOADER.load_pages(_pdf(4))

    assert caught.value.code is ErrorCode.PAGES_LIMIT_EXCEEDED


def test_rejects_the_tiff_when_it_has_more_frames_than_the_cap() -> None:
    frames = [Image.new("RGB", (10, 10), (index, 0, 0)) for index in range(4)]

    with pytest.raises(TooManyPagesError):
        LOADER.load_pages(_encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:]))


def test_ignores_what_the_file_claims_to_be_when_the_content_is_a_different_type() -> None:
    png_bytes = _encoded(Image.new("RGB", (20, 20), (9, 9, 9)), "PNG")

    assert len(LOADER.load_pages(png_bytes)) == 1  # a loader never sees a name or a declared type, only content


def test_reads_pixels_that_are_not_all_one_value_when_the_image_has_detail() -> None:
    noise = np.random.default_rng(1).integers(0, 255, (30, 30, 3), dtype=np.uint8)

    page = LOADER.load_pages(_encoded(Image.fromarray(noise), "PNG"))[0]

    assert np.array_equal(to_rgb_array(page), noise)
