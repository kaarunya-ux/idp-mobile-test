"""PDF pages -> images: chosen pages only, sized by DPI and capped, locked or damaged PDFs reported with a code."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError, PasswordRequiredError
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepRequest
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pdf_to_images import PdfiumPageRenderer

R = Representation
MakePdf = Callable[..., Path]
MakeRequest = Callable[..., StepRequest]
renderer = PdfiumPageRenderer(max_pages=1000, max_side_px=5000)


def _render(pdf: Path, target: R, make_request: MakeRequest, options: ConversionOptions | None = None) -> list[Path]:
    request = make_request(Converter.PDF_TO_IMAGES, R.PDF, target, [pdf], options)
    return list(renderer.convert(request).outputs)


def test_renders_every_page_at_the_requested_dpi_when_no_range_is_given(
    make_pdf: MakePdf, make_request: MakeRequest
) -> None:
    pdf = make_pdf("doc.pdf", sizes=[(200, 100), (100, 200)])

    outputs = _render(pdf, R.PNG, make_request, ConversionOptions(dpi=144))

    assert [o.name for o in outputs] == ["doc-page-0001.png", "doc-page-0002.png"]
    with Image.open(outputs[0]) as first, Image.open(outputs[1]) as second:
        assert first.size == (400, 200)  # 200 points at 144 dpi = 400 px
        assert second.size == (200, 400)
        assert tuple(round(v) for v in first.info["dpi"]) == (144, 144)


def test_renders_only_the_chosen_pages_in_order_when_a_range_is_given(
    make_pdf: MakePdf, make_request: MakeRequest
) -> None:
    pdf = make_pdf("doc.pdf", sizes=[(100, 100)] * 4)

    outputs = _render(pdf, R.JPEG, make_request, ConversionOptions(page_range=PageRange.parse("4,2")))

    assert [o.name for o in outputs] == ["doc-page-0004.jpg", "doc-page-0002.jpg"]


def test_lowers_the_dpi_when_a_page_would_exceed_the_size_cap(make_pdf: MakePdf, make_request: MakeRequest) -> None:
    pdf = make_pdf("poster.pdf", sizes=[(1000, 500)])
    request = make_request(Converter.PDF_TO_IMAGES, R.PDF, R.PNG, [pdf], ConversionOptions(dpi=600))

    (output,) = PdfiumPageRenderer(max_pages=10, max_side_px=800).convert(request).outputs

    with Image.open(output) as image:
        assert max(image.size) == 800


def test_draws_pages_to_fit_the_request_size_cap_when_one_is_given(
    make_pdf: MakePdf, make_request: MakeRequest
) -> None:
    pdf = make_pdf("a4.pdf", sizes=[(595, 842)])
    request = make_request(Converter.PDF_TO_IMAGES, R.PDF, R.JPEG, [pdf], ConversionOptions(dpi=600, max_side_px=400))

    (output,) = renderer.convert(request).outputs

    with Image.open(output) as image:
        assert image.size == (283, 400)


def test_counts_the_pages_it_rendered_when_done(make_pdf: MakePdf, make_request: MakeRequest) -> None:
    pdf = make_pdf("doc.pdf", sizes=[(100, 100)] * 3)

    result = renderer.convert(make_request(Converter.PDF_TO_IMAGES, R.PDF, R.PNG, [pdf]))

    assert result.page_count == 3


def test_reports_cnv_422_pdf_001_when_the_pdf_needs_a_password(make_pdf: MakePdf, make_request: MakeRequest) -> None:
    locked = make_pdf("locked.pdf", sizes=[(100, 100)], password="s3cret")

    with pytest.raises(PasswordRequiredError) as caught:
        _render(locked, R.PNG, make_request)

    assert caught.value.code == "CNV-422-PDF-001"


def test_reports_cnv_422_pdf_002_when_the_pdf_is_damaged(tmp_path: Path, make_request: MakeRequest) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis file was cut short")

    with pytest.raises(CorruptPdfError) as caught:
        _render(broken, R.PNG, make_request)

    assert caught.value.code == "CNV-422-PDF-002"


def test_refuses_with_cnv_422_limit_001_when_the_pdf_has_too_many_pages(
    make_pdf: MakePdf, make_request: MakeRequest
) -> None:
    pdf = make_pdf("long.pdf", sizes=[(50, 50)] * 3)
    request = make_request(Converter.PDF_TO_IMAGES, R.PDF, R.PNG, [pdf])

    with pytest.raises(LimitExceededError, match="more than 2 pages"):
        PdfiumPageRenderer(max_pages=2, max_side_px=5000).convert(request)
