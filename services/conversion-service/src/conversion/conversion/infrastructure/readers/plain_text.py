"""``TextReaderPort``: the plain text inside a file, by the libraries, and by OCR for pictures and scans (ADR 0003).

Word: paragraphs and table rows in order, a row's cells by tab. Excel: each sheet's rows, cells by tab. PowerPoint:
each slide's text boxes and tables. CSV: as it is. PDF: each page's text layer, and OCR for a page with less text than
``ocr_below_chars`` (a scan, or a scan with a stray line, as a browser prints one), keeping whichever reading has more.
Pictures: OCR. OCR is engine A in English only, loaded on the first picture, so a process that reads none never holds
the models. Pages are separated by a blank line.
"""

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import docx
import numpy as np
import openpyxl
import pptx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from PIL import Image, UnidentifiedImageError

from conversion.conversion.domain.exceptions import RouteNotFoundError, UnreadableImageError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.ports import PlainText
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.port import OcrEnginePort

_LANGUAGES = ("en",)
_POINTS_PER_INCH = 72
_PAGE_BREAK = "\n\n"


class PlainTextReader:
    def __init__(self, ocr: Callable[[], OcrEnginePort], *, ocr_below_chars: int, render_dpi: int) -> None:
        self._load_ocr = ocr
        self._ocr: OcrEnginePort | None = None
        self._loading = threading.Lock()
        self._ocr_below_chars = ocr_below_chars
        self._render_dpi = render_dpi

    def read_text(self, path: Path, source: Format) -> PlainText:
        readers: dict[Format, Callable[[Path], PlainText]] = {
            Format.DOCX: _docx,
            Format.XLSX: _xlsx,
            Format.PPTX: _pptx,
            Format.CSV: _csv,
            Format.PDF: self._pdf,
            Format.PNG: self._picture,
            Format.JPEG: self._picture,
        }
        reader = readers.get(source)
        if reader is None:
            raise RouteNotFoundError(source, Format.TXT)
        return reader(path)

    def _pdf(self, path: Path) -> PlainText:
        texts: list[str] = []
        with open_pdf(path) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    layer = str(text_page.get_text_range()).replace("\r", "").strip()
                    text_page.close()
                    if len(layer) < self._ocr_below_chars:
                        image = page.render(scale=self._render_dpi / _POINTS_PER_INCH).to_pil()
                        read = self._ocr_text(image)
                        layer = read if len(read) > len(layer) else layer
                finally:
                    page.close()
                texts.append(layer)
        return PlainText(text=_PAGE_BREAK.join(texts), pages=len(texts))

    def _picture(self, path: Path) -> PlainText:
        try:
            with Image.open(path) as image:
                return PlainText(text=self._ocr_text(image), pages=1)
        except (UnidentifiedImageError, OSError) as exc:
            raise UnreadableImageError from exc

    def _ocr_text(self, image: Image.Image) -> str:
        rgb = np.asarray(image.convert("RGB"))
        return self._engine().read_page(from_rgb_array(rgb, page_index=0), _LANGUAGES).text.strip()

    def _engine(self) -> OcrEnginePort:
        with self._loading:
            if self._ocr is None:
                self._ocr = self._load_ocr()
            return self._ocr


def _docx(path: Path) -> PlainText:
    document = docx.Document(str(path))
    lines: list[str] = []
    for block in _body_blocks(document):
        if isinstance(block, Paragraph):
            lines.append(block.text)
        else:
            lines += ["\t".join(cell.text for cell in row.cells) for row in block.rows]
    return PlainText(text="\n".join(lines).strip(), pages=1)


def _body_blocks(document: object) -> Iterator[Paragraph | DocxTable]:
    """Paragraphs and tables in the order they are written."""
    body = document.element.body  # type: ignore[attr-defined]  # python-docx has no types for the body element
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)  # type: ignore[arg-type]  # the document is the paragraph's parent
        elif child.tag.endswith("}tbl"):
            yield DocxTable(child, document)  # type: ignore[arg-type]  # the document is the table's parent


def _xlsx(path: Path) -> PlainText:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            rows = [
                "\t".join("" if v is None else str(v) for v in row).rstrip("\t")
                for row in sheet.iter_rows(values_only=True)
            ]
            sheets.append("\n".join(row for row in rows if row))
        return PlainText(text=_PAGE_BREAK.join(sheets).strip(), pages=len(sheets))
    finally:
        workbook.close()


def _pptx(path: Path) -> PlainText:
    slides = []
    for slide in pptx.Presentation(str(path)).slides:
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                parts += ["\t".join(cell.text for cell in row.cells) for row in shape.table.rows]
        slides.append("\n".join(part for part in parts if part.strip()))
    return PlainText(text=_PAGE_BREAK.join(slides).strip(), pages=len(slides))


def _csv(path: Path) -> PlainText:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")  # every byte is a character: an old export still reads
    return PlainText(text=text.replace("\r\n", "\n").strip(), pages=1)
