"""Renders PDF pages to PNG or JPEG with PDFium (IDP-03 ch. 3.4: ``pdf_to_images``).

Ported from ``services/conversions/python_scripts/pdf_to_png.py`` and ``pdf_to_jpg.py`` (Sumukh, Karunya),
moved from PyMuPDF (AGPL) to pypdfium2, as the standard's approved library list requires (build log entry 3).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image

from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError, PasswordRequiredError
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter

POINTS_PER_INCH = 72
_SAVE_FORMAT = {Representation.PNG: "PNG", Representation.JPEG: "JPEG"}
_SUFFIX = {Representation.PNG: ".png", Representation.JPEG: ".jpg"}


class PdfiumPageRenderer:
    """Writes one image per selected page, named ``<input>-page-0001.png`` and so on."""

    def __init__(self, *, max_pages: int, max_side_px: int) -> None:
        self._max_pages = max_pages
        self._max_side_px = max_side_px

    @property
    def converter(self) -> Converter:
        return Converter.PDF_TO_IMAGES

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs  # one PDF in; the planner never sends more
        outputs: list[Path] = []
        with open_pdf(source) as document:
            page_count = len(document)
            if page_count > self._max_pages:
                raise LimitExceededError(f"The PDF has more than {self._max_pages:,} pages.")
            options = request.options
            indexes = options.page_range.select(page_count) if options.page_range else range(page_count)
            for index in indexes:
                image, dpi = self._render(document, index, options.dpi)
                output = request.output_dir / f"{source.stem}-page-{index + 1:04d}{_SUFFIX[request.step.target]}"
                _save(image, output, request.step.target, dpi, options)
                outputs.append(output)
        return StepResult(outputs=tuple(outputs), page_count=len(outputs))

    def _render(self, document: pdfium.PdfDocument, index: int, dpi: int) -> tuple[Image.Image, int]:
        page = document[index]
        try:
            width_pt, height_pt = page.get_size()
            scale = min(dpi / POINTS_PER_INCH, self._max_side_px / max(width_pt, height_pt))  # cap the longest side
            image: Image.Image = page.render(scale=scale).to_pil()  # untyped library -> typed value here
            return image, round(scale * POINTS_PER_INCH)
        finally:
            page.close()


@contextmanager
def open_pdf(path: Path) -> Iterator[pdfium.PdfDocument]:
    """Open a PDF with PDFium, turning its errors into domain errors, and always close it."""
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium_raw.FPDF_ERR_PASSWORD:
            raise PasswordRequiredError from exc
        raise CorruptPdfError from exc
    with document:
        yield document


def _save(image: Image.Image, output: Path, target: Representation, dpi: int, options: ConversionOptions) -> None:
    if target is Representation.JPEG:
        image.convert("RGB").save(output, format="JPEG", quality=options.jpeg_quality, dpi=(dpi, dpi))
    else:
        image.save(output, format=_SAVE_FORMAT[target], dpi=(dpi, dpi))
