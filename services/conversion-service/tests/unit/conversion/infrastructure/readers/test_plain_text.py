"""Target TXT: the text inside each kind of file, by the libraries, and by OCR for pictures and scans. Made-up text."""

from collections.abc import Sequence
from pathlib import Path

import docx
import openpyxl
import pptx
import pytest
from tests.unit.conversion.conftest import MakeImage, MakePdf, MakeTextPdf

from conversion.conversion.domain.exceptions import RouteNotFoundError, UnreadableImageError
from conversion.conversion.domain.formats import Format
from conversion.conversion.infrastructure.readers.plain_text import PlainTextReader
from idp_ocr_engines.model import Box, OcrPage, OcrWord, PageImage


class FakeOcr:
    """Reads every picture as the same two lines, and counts the pictures."""

    def __init__(self) -> None:
        self.pages = 0

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        self.pages += 1
        words = (_word("MOCK", 0, 0), _word("RECEIPT", 0, 1), _word("Total", 1, 0), _word("575.50", 1, 1))
        return OcrPage(width=image.width, height=image.height, words=words)


def _word(text: str, line: int, column: int) -> OcrWord:
    box = Box(x=0.1 + column * 0.3, y=0.1 + line * 0.2, width=0.2, height=0.1)
    return OcrWord(text=text, box=box, confidence=0.9, line_id=line, block_id=0)


def _reader(ocr: FakeOcr, *, ocr_below_chars: int = 20) -> PlainTextReader:
    return PlainTextReader(lambda: ocr, ocr_below_chars=ocr_below_chars, render_dpi=72)  # type: ignore[arg-type, return-value]  # a stand-in engine


def test_reads_a_word_files_paragraphs_and_table_rows_in_order(tmp_path: Path) -> None:
    document = docx.Document()
    document.add_paragraph("Demo Invoice 1001")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Paper", "500.00"
    document.add_paragraph("Thank you")
    path = tmp_path / "invoice.docx"
    document.save(str(path))

    found = _reader(FakeOcr()).read_text(path, Format.DOCX)

    assert found.text == "Demo Invoice 1001\nPaper\t500.00\nThank you"


def test_reads_each_sheet_row_by_row_with_a_blank_line_between_sheets(tmp_path: Path) -> None:
    workbook = openpyxl.Workbook()
    workbook.active.append(["Item", "Qty"])
    workbook.active.append(["Pens", 5])
    workbook.create_sheet("Notes").append(["Checked"])
    path = tmp_path / "sales.xlsx"
    workbook.save(path)

    found = _reader(FakeOcr()).read_text(path, Format.XLSX)

    assert (found.text, found.pages) == ("Item\tQty\nPens\t5\n\nChecked", 2)


def test_reads_each_slides_text(tmp_path: Path) -> None:
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "IDP Demo"
    slide.placeholders[1].text = "Extraction and conversion"
    path = tmp_path / "deck.pptx"
    deck.save(str(path))

    found = _reader(FakeOcr()).read_text(path, Format.PPTX)

    assert (found.text, found.pages) == ("IDP Demo\nExtraction and conversion", 1)


def test_gives_a_csv_as_it_is(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_bytes(b"Item,Qty\r\nPens,5\r\n")

    assert _reader(FakeOcr()).read_text(path, Format.CSV).text == "Item,Qty\nPens,5"


def test_reads_a_digital_pdfs_text_layer_without_ocr(make_text_pdf: MakeTextPdf) -> None:
    ocr = FakeOcr()
    path = make_text_pdf("doc.pdf", ["A page of real text, long enough to keep.", "The second page of text here."])

    found = _reader(ocr).read_text(path, Format.PDF)

    assert found.text == "A page of real text, long enough to keep.\n\nThe second page of text here."
    assert (found.pages, ocr.pages) == (2, 0)


def test_reads_a_scanned_pdf_page_and_a_page_with_only_a_stray_line_with_ocr(
    make_pdf: MakePdf, make_text_pdf: MakeTextPdf
) -> None:
    ocr = FakeOcr()

    scanned = _reader(ocr).read_text(make_pdf("scan.pdf", sizes=[(200, 100)]), Format.PDF)
    stray = _reader(ocr).read_text(make_text_pdf("printed.pdf", ["11/12/26 3:45 PM"]), Format.PDF)

    assert scanned.text == stray.text == "MOCK RECEIPT\nTotal 575.50"
    assert ocr.pages == 2


def test_reads_a_picture_with_ocr(make_image: MakeImage) -> None:
    found = _reader(FakeOcr()).read_text(make_image("receipt.png", size=(80, 40)), Format.PNG)

    assert (found.text, found.pages) == ("MOCK RECEIPT\nTotal 575.50", 1)


def test_refuses_a_picture_it_cannot_open(tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a picture")

    with pytest.raises(UnreadableImageError):
        _reader(FakeOcr()).read_text(path, Format.PNG)


def test_has_no_text_route_for_formats_it_does_not_read(tmp_path: Path) -> None:
    with pytest.raises(RouteNotFoundError):
        _reader(FakeOcr()).read_text(tmp_path / "x.json", Format.JSON)
