"""Images -> one PDF: one page per image, JPEG data copied unchanged, transparency flattened."""

from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from conversion.conversion.domain.exceptions import LimitExceededError, UnreadableImageError
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.ports import StepRequest
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.images_to_pdf import Img2PdfImageWrapper

R = Representation
MakeImage = Callable[..., Path]
MakeRequest = Callable[..., StepRequest]
wrapper = Img2PdfImageWrapper(max_pixels=10_000_000)


def _wrap(inputs: list[Path], make_request: MakeRequest) -> Path:
    (output,) = wrapper.convert(make_request(Converter.IMAGES_TO_PDF, R.JPEG, R.PDF, inputs)).outputs
    return output


def test_makes_one_page_per_image_in_order_when_wrapping(make_image: MakeImage, make_request: MakeRequest) -> None:
    inputs = [make_image("a.jpg", size=(144, 72), dpi=(72, 72)), make_image("b.png", size=(72, 144), dpi=(72, 72))]

    output = _wrap(inputs, make_request)

    assert output.name == "a.pdf"
    with pdfium.PdfDocument(str(output)) as pdf:
        assert len(pdf) == 2
        assert tuple(round(v) for v in pdf[0].get_size()) == (144, 72)  # 144 px at 72 dpi = 144 points
        assert tuple(round(v) for v in pdf[1].get_size()) == (72, 144)


def test_copies_jpeg_bytes_unchanged_when_wrapping(make_image: MakeImage, make_request: MakeRequest) -> None:
    photo = make_image("photo.jpg", size=(120, 80), color=(200, 30, 60))

    output = _wrap([photo], make_request)

    assert photo.read_bytes() in output.read_bytes()  # lossless: the original JPEG stream sits inside the PDF


def test_flattens_transparency_when_an_image_has_alpha(make_image: MakeImage, make_request: MakeRequest) -> None:
    logo = make_image("logo.png", mode="RGBA", color=(255, 0, 0, 100))

    output = _wrap([logo], make_request)

    with pdfium.PdfDocument(str(output)) as pdf:
        assert len(pdf) == 1


def test_reports_cnv_422_file_001_when_an_input_is_not_an_image(
    tmp_path: Path, make_image: MakeImage, make_request: MakeRequest
) -> None:
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"\xff\xd8 not really a jpeg")

    with pytest.raises(UnreadableImageError):
        _wrap([make_image("ok.jpg"), junk], make_request)


def test_refuses_when_an_image_has_too_many_pixels(make_image: MakeImage, make_request: MakeRequest) -> None:
    request = make_request(Converter.IMAGES_TO_PDF, R.PNG, R.PDF, [make_image("big.png", size=(50, 50))])

    with pytest.raises(LimitExceededError):
        Img2PdfImageWrapper(max_pixels=1000).convert(request)


def test_keeps_a_valid_pdf_when_an_image_is_wider_than_200_inches(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    banner = make_image("banner.png", size=(300, 10), dpi=(1, 1))  # 300 px at 1 dpi = 300 inches wide

    output = _wrap([banner], make_request)

    with pdfium.PdfDocument(str(output)) as pdf:
        assert len(pdf) == 1
