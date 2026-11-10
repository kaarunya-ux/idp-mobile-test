"""The Tesseract adapter: a second reading of a page picture. The reading test needs the `tesseract` program and is
skipped without it (the worker image has it)."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from conversion.conversion.infrastructure.converters.tesseract_text import TesseractTextRecognizer, tesseract_available


def _picture(tmp_path: Path, text: str) -> Path:
    image = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(image).text((30, 50), text, fill="black", font=ImageFont.load_default(size=40))
    path = tmp_path / "page.png"
    image.save(path)
    return path


@pytest.mark.skipif(not tesseract_available(), reason="the tesseract program is not installed")
def test_reads_the_words_printed_on_a_picture(tmp_path: Path) -> None:
    text = TesseractTextRecognizer("eng", 30).read_text(_picture(tmp_path, "Invoice 2041 total payable"))

    assert "invoice" in text.casefold()
    assert "2041" in text


def test_returns_no_text_when_the_file_is_not_a_picture(tmp_path: Path) -> None:
    not_a_picture = tmp_path / "page.png"
    not_a_picture.write_bytes(b"not an image")

    assert TesseractTextRecognizer("eng", 30).read_text(not_a_picture) == ""
