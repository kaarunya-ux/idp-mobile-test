"""Facts about files that steer a job item: is a PDF scanned, and how many pages does an output have."""

import zipfile
from pathlib import Path

import pptx

from conversion.conversion.domain.exceptions import PasswordRequiredError
from conversion.conversion.domain.formats import Format
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf

TEXT_CHECK_PAGES = 5  # a PDF with no text in its first pages is treated as scanned
_SIGNATURES = ((b"%PDF-", Format.PDF), (b"\xff\xd8\xff", Format.JPEG), (b"\x89PNG\r\n\x1a\n", Format.PNG))
_ZIP_SIGNATURE = b"PK\x03\x04"
_OFFICE_PARTS = {"word/document.xml": Format.DOCX, "xl/workbook.xml": Format.XLSX, "ppt/presentation.xml": Format.PPTX}
_HEAD_BYTES = 8192


class PdfiumFileInspector:
    def detect_format(self, path: Path) -> Format | None:
        """By signature, then an Office file by the main part inside its ZIP. Text with no NUL byte can only be a CSV
        here; if it isn't one, the reader says so."""
        with path.open("rb") as stream:
            head = stream.read(_HEAD_BYTES)
        known = next((file_format for signature, file_format in _SIGNATURES if head.startswith(signature)), None)
        if known is not None:
            return known
        if head.startswith(_ZIP_SIGNATURE):
            return _office_format(path)
        return Format.CSV if head and b"\x00" not in head else None

    def is_scanned_pdf(self, path: Path) -> bool:
        """No characters on any of the first pages means there is no text layer to read (IDP-03 ch. 3.4)."""
        with open_pdf(path) as document:
            for index in range(min(len(document), TEXT_CHECK_PAGES)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    characters = int(text_page.count_chars())
                    text_page.close()
                finally:
                    page.close()
                if characters:
                    return False
        return True

    def count_pages(self, path: Path, file_format: Format) -> int | None:
        if file_format is Format.PDF:
            try:
                with open_pdf(path) as document:
                    return len(document)
            except PasswordRequiredError:  # an output the plan protected: Java gets no page count
                return None
        if file_format is Format.PPTX:
            return len(pptx.Presentation(str(path)).slides)
        if file_format.is_image:
            return 1
        return None  # DOCX flows into pages only when laid out; XLSX and CSV have none


def _office_format(path: Path) -> Format | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return None
    return next((file_format for part, file_format in _OFFICE_PARTS.items() if part in names), None)
