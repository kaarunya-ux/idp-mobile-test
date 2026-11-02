"""Scanned PDF or photo -> document model (IDP-03 ch. 3.4: ``ocr_to_model``): the reading step of the scanned
routes (scan or photo to Word, Excel or CSV).

Each page is drawn as a picture (a scanned PDF at ``AI_GATEWAY__RENDER_DPI``; a photo as it is, made upright) and
read by a ``PageReaderPort``: today the model-first reader through ai-gateway. The writers that follow (Word, Excel,
CSV) are the ones the digital routes already use. Registered only when ai-gateway is configured; without it these
routes keep answering ``CNV-422-ROUTE-001``.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from conversion.conversion.domain.document_model import DocumentModel
from conversion.conversion.domain.exceptions import LimitExceededError, OcrNotAvailableError, UnreadableImageError
from conversion.conversion.domain.ports import PageReaderPort, StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pdf_to_images import POINTS_PER_INCH, open_pdf
from conversion.conversion.infrastructure.model_file import model_path, write_model

_PDF_MAGIC = b"%PDF"


class ModelFirstOcrConverter:
    def __init__(self, reader: PageReaderPort, *, render_dpi: int, max_pages: int, max_pixels: int) -> None:
        self._reader = reader
        self._render_dpi = render_dpi
        self._max_pages = max_pages
        self._max_pixels = max_pixels

    @property
    def converter(self) -> Converter:
        return Converter.OCR_TO_MODEL

    def convert(self, request: StepRequest) -> StepResult:
        context = request.context
        if context is None:  # outside a job there is no account to read it for
            raise OcrNotAvailableError
        with tempfile.TemporaryDirectory(prefix="ocr-pages-") as scratch:
            pictures = list(self._pictures(request, Path(scratch)))
            pages = tuple(
                self._reader.read_page(picture, number, context) for number, picture in enumerate(pictures, 1)
            )
        output = model_path(request.output_dir, request.inputs[0])
        write_model(DocumentModel(pages=pages), output)
        return StepResult(outputs=(output,), page_count=len(pages))

    def _pictures(self, request: StepRequest, folder: Path) -> Iterator[Path]:
        count = 0
        for source in request.inputs:
            for picture in (
                self._pdf_pages(source, folder, request) if _is_pdf(source) else [self._photo(source, folder)]
            ):
                count += 1
                if count > self._max_pages:
                    raise LimitExceededError(f"Scanned documents are read up to {self._max_pages:,} pages.")
                yield picture

    def _pdf_pages(self, source: Path, folder: Path, request: StepRequest) -> Iterator[Path]:
        with open_pdf(source) as document:
            total = len(document)
            if total > self._max_pages:
                raise LimitExceededError(f"Scanned documents are read up to {self._max_pages:,} pages.")
            range_ = request.options.page_range
            for index in range_.select(total) if range_ else range(total):
                page = document[index]
                try:
                    image: Image.Image = page.render(scale=self._render_dpi / POINTS_PER_INCH).to_pil()
                finally:
                    page.close()
                output = folder / f"{source.stem}-page-{index + 1:04d}.png"
                image.convert("RGB").save(output, format="PNG")
                yield output

    def _photo(self, source: Path, folder: Path) -> Path:
        try:
            with Image.open(source) as image:
                if image.width * image.height > self._max_pixels:
                    raise LimitExceededError("The image has more pixels than this service accepts.")
                upright = ImageOps.exif_transpose(image).convert("RGB")
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise UnreadableImageError from exc
        output = folder / f"{source.stem}.png"
        upright.save(output, format="PNG")
        return output


def _is_pdf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(len(_PDF_MAGIC)) == _PDF_MAGIC
