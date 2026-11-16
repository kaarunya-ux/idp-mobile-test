"""The scanned-PDF check that sends a file to OCR, and the page counts reported as usage."""

import zipfile
from pathlib import Path

import pikepdf
import pptx
import pytest
from pikepdf import Dictionary, Name

from conversion.conversion.domain.formats import Format
from conversion.conversion.infrastructure.inspection import TEXT_CHECK_PAGES, PdfiumFileInspector


def _pdf(path: Path, pages_with_text: set[int], *, pages: int = 1) -> Path:
    pdf = pikepdf.new()
    font = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/Helvetica"))
    for index in range(pages):
        page = pdf.add_blank_page(page_size=(612, 792))
        page.Resources = Dictionary(Font=Dictionary(F1=font))
        page.Contents = pdf.make_stream(b"BT /F1 12 Tf 72 700 Td (Hello) Tj ET" if index in pages_with_text else b"")
    pdf.save(path)
    return path


def test_treats_a_pdf_with_text_on_an_early_page_as_digital(tmp_path: Path) -> None:
    assert not PdfiumFileInspector().is_scanned_pdf(_pdf(tmp_path / "a.pdf", {2}, pages=3))


def test_treats_a_pdf_with_no_text_in_its_first_pages_as_scanned(tmp_path: Path, make_pdf) -> None:
    late_text = _pdf(tmp_path / "late.pdf", {TEXT_CHECK_PAGES}, pages=TEXT_CHECK_PAGES + 1)

    assert PdfiumFileInspector().is_scanned_pdf(make_pdf("scan.pdf", sizes=[(200, 100)]))
    assert PdfiumFileInspector().is_scanned_pdf(late_text)


def test_counts_pages_of_pdfs_slides_and_images_but_not_sheets(tmp_path: Path) -> None:
    deck = tmp_path / "deck.pptx"
    presentation = pptx.Presentation()
    for _ in range(2):
        presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(str(deck))
    inspector = PdfiumFileInspector()

    assert inspector.count_pages(_pdf(tmp_path / "p.pdf", set(), pages=3), Format.PDF) == 3
    assert inspector.count_pages(deck, Format.PPTX) == 2
    assert inspector.count_pages(tmp_path / "x.png", Format.PNG) == 1
    assert inspector.count_pages(tmp_path / "x.csv", Format.CSV) is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7 made up", Format.PDF),
        (b"\xff\xd8\xff\xe0 made up", Format.JPEG),
        (b"\x89PNG\r\n\x1a\n made up", Format.PNG),
        (b"name,amount\nTest A,12.50\n", Format.CSV),
        (b"", None),
        (b"\x00\x01\x02 binary", None),
        (b"PK\x03\x04 not really a zip", None),
    ],
)
def test_detects_the_format_from_the_content_whatever_the_file_is_called(
    tmp_path: Path, content: bytes, expected: Format | None
) -> None:
    path = tmp_path / "report.docx"  # a name that lies
    path.write_bytes(content)

    assert PdfiumFileInspector().detect_format(path) is expected


@pytest.mark.parametrize(
    ("part", "expected"),
    [
        ("word/document.xml", Format.DOCX),
        ("xl/workbook.xml", Format.XLSX),
        ("ppt/presentation.xml", Format.PPTX),
        ("something/else.xml", None),
    ],
)
def test_detects_an_office_file_by_the_main_part_in_its_zip(tmp_path: Path, part: str, expected: Format | None) -> None:
    path = tmp_path / "upload"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(part, "<made-up/>")

    assert PdfiumFileInspector().detect_format(path) is expected
