"""``DigitalTextReaderPort`` with PDFium: a digital PDF's first page as words with positions (IDP-03 ch. 2.6 stage 5).

A PDF made by software (e-PAN, e-Aadhaar, a bank letter) carries its text exactly; OCR would only add mistakes.
Characters are grouped into words at the spaces PDFium reports (and at gaps wider than the text height), then into
lines and blocks by the engine library's own layout rules, so the rest of the pipeline sees what an OCR engine gives.

``None`` means "read it with OCR": not a PDF, a page with too little text (a scan inside a PDF), a picture page (a photo
or scan saved as a PDF: its text is a header or a scan's hidden layer, see ``pdf_pictures``), or a rotated page (its
text positions would need turning; rare for software-made PDFs).
"""

from dataclasses import dataclass

import pypdfium2 as pdfium

from extraction.pipeline.infrastructure.digital.pdf_pictures import picture_of
from idp_ocr_engines.layout import RecognizedPiece, assign_line_and_block_ids
from idp_ocr_engines.model import Box, OcrPage

_PDF_MAGIC = b"%PDF"
_MIN_CHARACTERS = 20  # fewer than this on the page: it is a scan, or nearly empty
_GAP_FRACTION = 1.0  # PDFium reports the spaces; a gap wider than the text height also starts a new word


@dataclass(slots=True)
class _Word:
    text: str
    left: float
    bottom: float
    right: float
    top: float


class PdfiumTextReader:
    def __init__(self, *, dpi: int, max_side_px: int = 10_000, picture_min_fraction: float = 1.1) -> None:
        self._scale = dpi / 72  # OcrPage sizes are in pixels at the rendering DPI, as the page images are
        self._max_side_px = max_side_px  # and capped on the long side the same way
        self._picture_min_fraction = picture_min_fraction  # above 1: no page is a picture page

    def read_first_page(self, content: bytes) -> OcrPage | None:
        if not content.startswith(_PDF_MAGIC):
            return None
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError:
            return None  # the page loader reports a damaged PDF with its own code
        try:
            return self._read(document[0])
        finally:
            document.close()

    def _read(self, page: pdfium.PdfPage) -> OcrPage | None:
        try:
            if page.get_rotation() != 0 or picture_of(page, min_fraction=self._picture_min_fraction) is not None:
                return None
            width, height = (float(v) for v in page.get_size())
            text_page = page.get_textpage()
            try:
                words = _words(text_page)
            finally:
                text_page.close()
        finally:
            page.close()
        if sum(len(w.text) for w in words) < _MIN_CHARACTERS:
            return None
        pieces = [
            RecognizedPiece(
                text=w.text,
                box=Box(
                    x=max(0.0, w.left / width),
                    y=max(0.0, (height - w.top) / height),
                    width=max(1e-6, min(1.0, (w.right - w.left) / width)),
                    height=max(1e-6, min(1.0, (w.top - w.bottom) / height)),
                ),
                confidence=1.0,
            )
            for w in words
        ]
        scale = min(self._scale, self._max_side_px / max(width, height, 1.0))
        return OcrPage(
            width=round(width * scale),
            height=round(height * scale),
            words=assign_line_and_block_ids(pieces),
        )


def _words(text_page: pdfium.PdfTextPage) -> list[_Word]:
    words: list[_Word] = []
    current: _Word | None = None
    for index in range(text_page.count_chars()):
        char = text_page.get_text_range(index, 1)
        if not char.strip():
            current = None
            continue
        left, bottom, right, top = text_page.get_charbox(index)
        gap_limit = (top - bottom) * _GAP_FRACTION
        if current is not None and left - current.right <= gap_limit and abs(bottom - current.bottom) < (top - bottom):
            current.text += char
            current.right, current.top = max(current.right, right), max(current.top, top)
            current.bottom = min(current.bottom, bottom)
        else:
            current = _Word(text=char, left=left, bottom=bottom, right=right, top=top)
            words.append(current)
    return words
