"""Wraps images into one PDF without re-encoding them (IDP-03 ch. 3.4: ``images_to_pdf``, img2pdf).

JPEG data is copied into the PDF byte for byte, so nothing is lost. Transparent images are first laid on white,
because img2pdf refuses alpha channels; that is the only change made to any pixel.
"""

import tempfile
from pathlib import Path

import img2pdf

from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pillow_images import flatten_on_white, has_transparency, open_image


class Img2PdfImageWrapper:
    """Writes one PDF with one page per input image, in input order, named after the first image."""

    def __init__(self, *, max_pixels: int) -> None:
        self._max_pixels = max_pixels

    @property
    def converter(self) -> Converter:
        return Converter.IMAGES_TO_PDF

    def convert(self, request: StepRequest) -> StepResult:
        output = request.output_dir / f"{request.inputs[0].stem}.pdf"
        with tempfile.TemporaryDirectory(prefix="flatten-") as scratch:  # always cleaned up (FIL-06)
            pages = [
                self._prepare(source, Path(scratch) / f"page-{position:04d}.png")
                for position, source in enumerate(request.inputs, start=1)
            ]
            with output.open("wb") as stream:  # streamed to disk, never held whole in memory (FIL-07)
                # Pages over the PDF limit of 200 inches are kept valid by img2pdf's default UserUnit scaling.
                img2pdf.convert([str(page) for page in pages], outputstream=stream, rotation=img2pdf.Rotation.ifvalid)
        return StepResult(outputs=(output,), page_count=len(pages))

    def _prepare(self, source: Path, flattened: Path) -> Path:
        """Check ``source`` opens and isn't too big; return it, or a copy flattened on white if it's transparent."""
        with open_image(source, max_pixels=self._max_pixels) as image:
            if not has_transparency(image):
                return source  # embedded unchanged
            flatten_on_white(image).save(flattened, format="PNG")
        return flattened
