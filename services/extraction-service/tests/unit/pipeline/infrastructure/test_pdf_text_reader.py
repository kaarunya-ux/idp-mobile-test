"""Reading a digital PDF's text layer instead of OCR (IDP-03 ch. 2.6 stage 5)."""

import pytest

from extraction.pipeline.infrastructure.digital.pdf_text_reader import PdfiumTextReader

reader = PdfiumTextReader(dpi=300)


def _pdf(lines: list[tuple[int, int, str]], *, rotate: int = 0, width: int = 600, height: int = 400) -> bytes:
    """A minimal PDF with Helvetica text at (x, y) points from the bottom left; PDFium rebuilds the cross-reference."""
    content = "".join(f"BT /F1 14 Tf {x} {y} Td ({text}) Tj ET\n" for x, y, text in lines).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Rotate {rotate} "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
    ]
    body = b"%PDF-1.7\n" + b"".join(b"%d 0 obj\n" % (n + 1) + o + b"\nendobj\n" for n, o in enumerate(objects))
    return body + b"trailer\n<< /Root 1 0 R /Size 6 >>\n%%EOF\n"


def test_reads_words_in_lines_with_positions_when_the_pdf_has_a_text_layer() -> None:
    page = reader.read_first_page(_pdf([(60, 340, "Permanent Account Number"), (60, 300, "ABCPE1234F")]))

    assert page is not None
    assert page.text == "Permanent Account Number\nABCPE1234F"
    assert (page.width, page.height) == (2500, 1667)  # pixels at 300 dpi, like the rendered page
    number = next(w for w in page.words if w.text == "ABCPE1234F")
    assert number.box.x == pytest.approx(0.1, abs=0.01)
    assert 0.2 < number.box.y < 0.3  # from the top
    assert number.confidence == 1.0


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xd8\xff\xe0 a jpeg",  # not a PDF
        _pdf([(60, 300, "Hi")]),  # too little text: a scan inside a PDF
        _pdf([(60, 300, "A digital page that is turned sideways")], rotate=90),
        b"%PDF-1.7 damaged",
    ],
)
def test_leaves_the_page_to_ocr_when_it_has_no_usable_text_layer(content: bytes) -> None:
    assert reader.read_first_page(content) is None
