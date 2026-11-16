"""Digital PDF -> model: headings by size, paragraphs from lines, bullets, ruled tables, PDF errors, full routes."""

import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Self

import docx
import openpyxl
import pikepdf
import pytest
from pdfplumber.utils.exceptions import PdfminerException
from pikepdf import Dictionary, Name

from conversion.bootstrap import build_convert_file_service
from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.domain.document_model import Alignment, DocumentModel, Picture, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError, PasswordRequiredError
from conversion.conversion.domain.formats import Format, Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepRequest
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters import pdf_to_model
from conversion.conversion.infrastructure.converters.office_to_pdf import LibreOfficePdfConverter
from conversion.conversion.infrastructure.converters.pdf_to_model import PdfplumberReader
from conversion.conversion.infrastructure.model_file import read_model
from conversion.core.config import Settings

R = Representation
MakeRequest = Callable[..., StepRequest]
MakePdf = Callable[..., Path]
PAGE_WIDTH, PAGE_HEIGHT = 612, 792  # US Letter, in points


class PdfBuilder:
    """Draws text in Helvetica and ruled grids on US Letter pages, measured from the top-left like pdfplumber."""

    def __init__(self) -> None:
        self._pages: list[bytes] = [b""]

    def text(self, x: float, top: float, text: str, *, size: int = 11, bold: bool = False) -> Self:
        escaped = text.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        font = b"F2" if bold else b"F1"
        self._pages[-1] += b"BT /%s %d Tf %.1f %.1f Td (%s) Tj ET\n" % (
            font,
            size,
            x,
            PAGE_HEIGHT - top - size,
            escaped,
        )
        return self

    def grid(self, x: float, top: float, rows: list[list[str]], *, width: int = 120, height: int = 20) -> Self:
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                left, cell_top = x + column * width, top + row_index * height
                self._pages[-1] += b"%.1f %.1f %d %d re S\n" % (left, PAGE_HEIGHT - cell_top - height, width, height)
                self.text(left + 4, cell_top + 5, value)
        return self

    def rule(self, x0: float, x1: float, top: float) -> Self:
        """A thin filled bar, as a browser draws a table row's bottom border."""
        self._pages[-1] += b"%.1f %.1f %.1f 0.7 re f\n" % (x0, PAGE_HEIGHT - top - 0.7, x1 - x0)
        return self

    def dot(self, x: float, top: float, size: float = 3.0) -> Self:
        """A small filled circle (four curves), as a browser draws a bullet."""
        r, k = size / 2, size / 2 * 0.55
        cx, cy = x + r, PAGE_HEIGHT - top - r
        self._pages[-1] += (
            b"%.2f %.2f m %.2f %.2f %.2f %.2f %.2f %.2f c %.2f %.2f %.2f %.2f %.2f %.2f c "
            b"%.2f %.2f %.2f %.2f %.2f %.2f c %.2f %.2f %.2f %.2f %.2f %.2f c f\n"
            % (
                cx + r,
                cy,
                cx + r,
                cy + k,
                cx + k,
                cy + r,
                cx,
                cy + r,
                cx - k,
                cy + r,
                cx - r,
                cy + k,
                cx - r,
                cy,
                cx - r,
                cy - k,
                cx - k,
                cy - r,
                cx,
                cy - r,
                cx + k,
                cy - r,
                cx + r,
                cy - k,
                cx + r,
                cy,
            )
        )
        return self

    def picture(self, x: float, top: float, width: float, height: float) -> Self:
        """A small made-up image drawn at this size, as /Im0 in the page's resources."""
        self._pages[-1] += b"q %.1f 0 0 %.1f %.1f %.1f cm /Im0 Do Q\n" % (width, height, x, PAGE_HEIGHT - top - height)
        return self

    def page(self) -> Self:
        self._pages.append(b"")
        return self

    def save(self, path: Path) -> Path:
        pdf = pikepdf.new()
        fonts = Dictionary(F1=_font("/Helvetica"), F2=_font("/Helvetica-Bold"))
        for content in self._pages:
            page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
            image = pdf.make_stream(
                bytes([200, 40, 40]) * 4,
                Type=Name.XObject,
                Subtype=Name.Image,
                Width=2,
                Height=2,
                BitsPerComponent=8,
                ColorSpace=Name.DeviceRGB,
            )
            page.Resources = Dictionary(Font=fonts, XObject=Dictionary(Im0=image))
            page.Contents = pdf.make_stream(content)
        pdf.save(path)
        return path


def _font(base: str) -> Dictionary:
    return Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name(base), Encoding=Name.WinAnsiEncoding)


def _read(
    pdf: PdfBuilder, tmp_path: Path, make_request: MakeRequest, *, max_pages: int = 100, **options: str
) -> DocumentModel:
    source = pdf.save(tmp_path / "scan.pdf")
    request = make_request(Converter.PDF_TO_MODEL, R.PDF, R.DOCUMENT_MODEL, [source], ConversionOptions(**options))
    (output,) = PdfplumberReader(max_pages=max_pages).convert(request).outputs
    return read_model(output)


def _texts(model: DocumentModel, page: int = 0) -> list[TextBlock]:
    return [block for block in model.pages[page].blocks if isinstance(block, TextBlock)]


def test_finds_headings_by_size_and_merges_lines_into_paragraphs(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 72, "Annual Report", size=20, bold=True)
        .text(72, 110, "Summary", size=14)
        .text(72, 140, "This paragraph starts on one line")
        .text(72, 154, "and carries on to the next.")
        .text(72, 190, "A second paragraph, further down.")
    )

    blocks = _texts(_read(pdf, tmp_path, make_request))

    assert blocks == [
        TextBlock(text="Annual Report", kind=TextKind.HEADING, level=1, bold=True),
        TextBlock(text="Summary", kind=TextKind.HEADING, level=2),
        TextBlock(text="This paragraph starts on one line and carries on to the next."),
        TextBlock(text="A second paragraph, further down."),
    ]


def test_reads_bullets_and_numbers_as_list_items_with_their_depth(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(90, 72, "\u2022 First point")  # first-level bullets are indented in Word too
        .text(108, 86, "- Nested point")
        .text(90, 100, "2. Numbered point")
        .text(72, 140, "Body text to set the body size, long enough to count the most.")
    )

    items = [(b.kind, b.level, b.text) for b in _texts(_read(pdf, tmp_path, make_request))][:3]

    assert items == [
        (TextKind.LIST_ITEM, 1, "First point"),  # the bullet glyph goes: Word draws its own
        (TextKind.LIST_ITEM, 2, "Nested point"),
        (TextKind.LIST_ITEM, 1, "2. Numbered point"),  # numbers stay: they carry meaning
    ]


BODY = "Body text to set the body size, long enough to count the most of all the text on the page."


def test_reads_a_table_drawn_with_only_a_rule_under_each_row(tmp_path: Path, make_request: MakeRequest) -> None:
    columns = (72.0, 200.0, 400.0)
    pdf = PdfBuilder().text(72, 60, BODY)
    for top, cells in (
        (100, ("Service", "Port")),
        (120, ("conversion-", "8001")),
        (134, ("service", "")),
        (154, ("ai-gateway", "8002")),
    ):
        for left, cell in zip(columns, cells, strict=False):
            if cell:
                pdf.text(left + 4, top, cell)
    for top in (115.0, 115.0, 149.0):  # the header's rule drawn twice, as on a table's next page
        pdf.rule(72, 200, top).rule(200, 400, top)
    pdf.text(72, 200, "A paragraph after the table.")

    model = _read(pdf, tmp_path, make_request)

    (table,) = model.pages[0].tables
    assert table == Table(
        rows=(("Service", "Port"), ("conversion-service", "8001"), ("ai-gateway", "8002")), header_rows=1
    )
    assert [b.text for b in _texts(model)] == [BODY, "A paragraph after the table."]


def test_reads_text_as_text_when_only_one_rule_is_segmented(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 60, BODY)
        .text(76, 100, "Name")
        .text(204, 100, "Date")
        .rule(72, 200, 115)
        .rule(200, 400, 115)
    )

    model = _read(pdf, tmp_path, make_request)

    assert model.pages[0].tables == []


def test_keeps_text_set_a_few_points_lower_in_its_sentence(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 100, "Run the checks with")
        .text(175, 103.1, "make check")  # inline code: a browser sets it 3.1 pt lower
        .text(240, 100, "before you push,")
        .text(72, 114, "then open the pull request.")
    )

    assert [b.text for b in _texts(_read(pdf, tmp_path, make_request))] == [
        "Run the checks with make check before you push, then open the pull request."
    ]


def test_reads_lines_after_drawn_dots_as_list_items_at_their_markers_level(
    tmp_path: Path, make_request: MakeRequest
) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 60, BODY)
        .dot(90, 104)
        .text(101, 100, "First point")  # the text starts after the dot: the dot is where the item starts
        .dot(118, 118)
        .text(129, 114, "Nested point")
        .text(90, 128, "2. Numbered point")
    )

    items = [(b.kind, b.level, b.text) for b in _texts(_read(pdf, tmp_path, make_request))][1:]

    assert items == [
        (TextKind.LIST_ITEM, 1, "First point"),
        (TextKind.LIST_ITEM, 2, "Nested point"),
        (TextKind.LIST_ITEM, 1, "2. Numbered point"),
    ]


def test_leaves_out_a_header_and_footer_repeated_on_every_page(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = PdfBuilder()
    for number in (1, 2):
        if number > 1:
            pdf.page()
        pdf.text(30, 12, "report.md 2026-01-01", size=7).text(72, 100, f"Page text {number}.").text(
            560, 770, f"{number} / 2", size=7
        )

    model = _read(pdf, tmp_path, make_request)

    assert [[b.text for b in _texts(model, page)] for page in (0, 1)] == [["Page text 1."], ["Page text 2."]]


def test_joins_a_word_broken_at_a_hyphen_across_lines(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = PdfBuilder().text(72, 100, "The files go to the conversion-").text(72, 114, "service for reading.")

    assert [b.text for b in _texts(_read(pdf, tmp_path, make_request))] == [
        "The files go to the conversion-service for reading."
    ]


def test_keeps_a_picture_where_it_sits_among_the_text_at_the_width_it_is_shown(
    tmp_path: Path, make_request: MakeRequest
) -> None:
    pdf = PdfBuilder().text(72, 60, "Above the picture.").picture(72, 100, 200, 100).text(72, 260, "Below the picture.")

    blocks = _read(pdf, tmp_path, make_request).pages[0].blocks

    assert [type(block) for block in blocks] == [TextBlock, Picture, TextBlock]
    picture = blocks[1]
    assert isinstance(picture, Picture)
    assert picture.width_pt == pytest.approx(200, abs=1)
    assert picture.data.startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize(("width", "height"), [(10, 10), (PAGE_WIDTH, PAGE_HEIGHT)])
def test_leaves_out_a_mark_too_small_to_be_a_picture_and_the_background_of_a_page(
    tmp_path: Path, make_request: MakeRequest, width: float, height: float
) -> None:
    pdf = PdfBuilder().picture(0, 0, width, height).text(72, 300, "Some text on the page.")

    blocks = _read(pdf, tmp_path, make_request).pages[0].blocks

    assert [type(block) for block in blocks] == [TextBlock]


def test_writes_a_pdfs_picture_into_the_word_file_on_the_full_route(tmp_path: Path) -> None:
    source = PdfBuilder().text(72, 60, "A report with a picture.").picture(72, 100, 200, 100).save(tmp_path / "r.pdf")
    output = tmp_path / "out"

    result = build_convert_file_service(Settings(_env_file=None)).convert(
        ConvertFileCommand(source=Format.PDF, target=Format.DOCX, inputs=(source,), output_dir=output)
    )

    assert len(docx.Document(str(result.outputs[0])).inline_shapes) == 1


def test_keeps_body_text_left_aligned_when_wide_margins_put_it_in_the_middle_of_the_page(
    tmp_path: Path, make_request: MakeRequest
) -> None:
    pdf = (  # about 70% of the page wide, and centred on it by the margins alone, as in Word's default layout
        PdfBuilder()
        .text(90, 100, "A made-up paragraph whose first line is long enough to wrap at the")
        .text(90, 114, "right margin of a page with the wide margins that Word gives by default.")
        .text(90, 150, "A second made-up paragraph, also starting at the left margin of the page.")
    )

    assert {block.alignment for block in _texts(_read(pdf, tmp_path, make_request))} == {Alignment.LEFT}


def test_keeps_centred_and_right_aligned_lines(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 72, "A long left-aligned line, wider than a centred title would be on this page.")
        .text(280, 120, "Centred")
        .text(470, 160, "Right edge text")  # ends furthest right, so it sets the text's right margin
        .text(72, 200, "Short left")
    )

    alignments = [b.alignment for b in _texts(_read(pdf, tmp_path, make_request))]

    assert alignments == [Alignment.LEFT, Alignment.CENTER, Alignment.RIGHT, Alignment.LEFT]


def test_reads_a_ruled_table_in_place_without_repeating_its_text(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = (
        PdfBuilder()
        .text(72, 72, "Before the table.")
        .grid(72, 100, [["item", "qty"], ["Paper", "10"], ["Ink", "2"]])
        .text(72, 200, "After the table.")
    )

    blocks = _read(pdf, tmp_path, make_request).pages[0].blocks

    assert blocks == (
        TextBlock(text="Before the table."),
        Table(rows=(("item", "qty"), ("Paper", "10"), ("Ink", "2")), header_rows=1),
        TextBlock(text="After the table."),
    )


@pytest.mark.parametrize(
    ("rows", "expected_text"),
    [([["Important"], ["notice"]], ["Important notice"]), ([["", ""], ["", ""]], [])],
)
def test_reads_a_one_column_frame_as_text_and_skips_an_empty_grid(
    tmp_path: Path, make_request: MakeRequest, rows: list[list[str]], expected_text: list[str]
) -> None:
    model = _read(PdfBuilder().grid(72, 72, rows, width=200, height=14), tmp_path, make_request)

    assert model.tables == []
    assert [b.text for b in _texts(model)] == expected_text


def test_reads_only_the_selected_pages_when_a_page_range_is_given(tmp_path: Path, make_request: MakeRequest) -> None:
    pdf = PdfBuilder().text(72, 72, "One").page().text(72, 72, "Two").page()  # the third page is empty

    model = _read(pdf, tmp_path, make_request, page_range=PageRange.parse("2-3"))

    assert [[b.text for b in _texts(model, page)] for page in range(2)] == [["Two"], []]


def test_refuses_a_pdf_with_more_pages_than_the_limit(tmp_path: Path, make_request: MakeRequest) -> None:
    with pytest.raises(LimitExceededError, match="more than 1 pages"):
        _read(PdfBuilder().page(), tmp_path, make_request, max_pages=1)


def test_reports_the_pdf_errors_when_the_file_is_locked_or_damaged(
    tmp_path: Path, make_request: MakeRequest, make_pdf: MakePdf
) -> None:
    locked = make_pdf("locked.pdf", sizes=[(100, 100)], password="secret")
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.7 not really")
    reader = PdfplumberReader(max_pages=10)

    for source, error in [(locked, PasswordRequiredError), (damaged, CorruptPdfError)]:
        with pytest.raises(error):
            reader.convert(make_request(Converter.PDF_TO_MODEL, R.PDF, R.DOCUMENT_MODEL, [source]))


def test_reports_a_damaged_pdf_when_pdfminer_cannot_parse_what_pdfium_repaired(
    tmp_path: Path, make_request: MakeRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_: Path) -> None:
        raise PdfminerException("unparsable")

    monkeypatch.setattr(pdf_to_model.pdfplumber, "open", refuse)

    with pytest.raises(CorruptPdfError):
        _read(PdfBuilder().text(72, 72, "x"), tmp_path, make_request)


def test_turns_a_digital_pdf_into_word_and_excel_through_the_model(tmp_path: Path) -> None:
    source = (
        PdfBuilder()
        .text(72, 72, "Invoice", size=18)
        .grid(72, 110, [["item", "qty"], ["Paper", "10"]])
        .text(72, 180, "Thank you.")
        .save(tmp_path / "invoice.pdf")
    )
    service = build_convert_file_service(Settings(_env_file=None))

    word = service.convert(
        ConvertFileCommand(source=Format.PDF, target=Format.DOCX, inputs=(source,), output_dir=tmp_path / "w")
    )
    excel = service.convert(
        ConvertFileCommand(source=Format.PDF, target=Format.XLSX, inputs=(source,), output_dir=tmp_path / "x")
    )

    document = docx.Document(str(word.outputs[0]))
    assert [(p.style.name, p.text) for p in document.paragraphs if p.style is not None] == [
        ("Heading 1", "Invoice"),
        ("Normal", "Thank you."),
    ]
    assert [[c.text for c in row.cells] for row in document.tables[0].rows] == [["item", "qty"], ["Paper", "10"]]
    sheet = openpyxl.load_workbook(excel.outputs[0]).active
    assert sheet is not None
    assert [[c.value for c in row] for row in sheet.iter_rows()] == [["item", "qty"], ["Paper", 10]]
    assert word.route.converters == (Converter.PDF_TO_MODEL, Converter.MODEL_TO_DOCX)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_reads_word_bullets_and_headings_back_from_a_pdf_libreoffice_made(
    tmp_path: Path, make_request: MakeRequest
) -> None:
    document = docx.Document()
    document.add_heading("Report", 1)
    document.add_paragraph("Exports", style="List Bullet")  # drawn with Symbol's private-area bullet, U+F0B7
    document.add_paragraph("Online", style="List Bullet 2")
    document.add_paragraph("Body text, long enough to be the most common size on the page.")
    source = tmp_path / "report.docx"
    document.save(str(source))
    office = LibreOfficePdfConverter(binary="soffice", timeout=timedelta(seconds=60))
    (pdf,) = office.convert(make_request(Converter.OFFICE_TO_PDF, R.DOCX, R.PDF, [source])).outputs

    (output,) = (
        PdfplumberReader(max_pages=10)
        .convert(make_request(Converter.PDF_TO_MODEL, R.PDF, R.DOCUMENT_MODEL, [pdf]))
        .outputs
    )

    assert [(b.kind, b.level, b.text) for b in _texts(read_model(output))][:3] == [
        (TextKind.HEADING, 1, "Report"),
        (TextKind.LIST_ITEM, 1, "Exports"),
        (TextKind.LIST_ITEM, 2, "Online"),
    ]
