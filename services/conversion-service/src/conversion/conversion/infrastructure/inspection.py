"""Facts about files that steer a job item: is a PDF scanned, and how many pages does an output have."""

from pathlib import Path

import pptx

from conversion.conversion.domain.formats import Format
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf

TEXT_CHECK_PAGES = 5  # a PDF with no text in its first pages is treated as scanned


class PdfiumFileInspector:
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
            with open_pdf(path) as document:
                return len(document)
        if file_format is Format.PPTX:
            return len(pptx.Presentation(str(path)).slides)
        if file_format.is_image:
            return 1
        return None  # DOCX flows into pages only when laid out; XLSX and CSV have none
