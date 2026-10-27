"""Merge PDFs with pikepdf (IDP-03 ch. 3.2, ``worker-pdf``; IDP-00 ``PDF_MERGE``).

Pages are copied as they are: no re-rendering, so text stays selectable and quality is unchanged. Bookmarks and
form fields of the inputs are not carried over. An encrypted input is refused, even one that opens without a
password: merging it would drop its print and copy restrictions.
"""

from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

import pikepdf

from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError, PasswordRequiredError


class PikepdfMerger:
    def __init__(self, *, max_pages: int) -> None:
        self._max_pages = max_pages

    def merge(self, pdfs: Sequence[Path], output: Path) -> int:
        with ExitStack() as stack, pikepdf.new() as merged:  # inputs stay open until the merged file is saved
            for path in pdfs:
                source = stack.enter_context(_open(path))
                if len(merged.pages) + len(source.pages) > self._max_pages:
                    raise LimitExceededError(f"The merged PDF would have more than {self._max_pages:,} pages.")
                merged.pages.extend(source.pages)
            merged.save(output)
            return len(merged.pages)


def _open(path: Path) -> pikepdf.Pdf:
    try:
        source = pikepdf.open(path)
    except pikepdf.PasswordError as exc:
        raise PasswordRequiredError from exc
    except pikepdf.PdfError as exc:
        raise CorruptPdfError from exc
    if source.is_encrypted:
        source.close()
        raise PasswordRequiredError
    return source
