"""Stage SCAN and page loading: the real type of a file, its size, and its pages as images (IDP-03 ch. 2.6 stages 1-2).

The file type comes from the content, never from a name or a declared type (FIL-03). Size, page count and pixel count
are capped before the expensive work starts, to refuse decompression bombs (FIL-05). Images are decoded with their EXIF
rotation applied, so a phone photo taken sideways arrives upright. A photo or scan that holds a card's front and back
side by side or one above the other becomes two pages, the same as the two sides sent as two files.
"""

import io
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pypdfium2 as pdfium
from filetype import guess_extension
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

from extraction.pipeline.domain.exceptions import FileRejectedError, TooManyPagesError
from extraction.pipeline.infrastructure.digital.pdf_pictures import picture_of
from extraction.pipeline.infrastructure.preprocess.card_detector import TwoCards, find_two_cards
from idp_ocr_engines.engines.arrays import from_rgb_array, to_rgb_array
from idp_ocr_engines.model import PageImage

_IMAGE_TYPES = frozenset({"jpg", "png", "webp", "tif", "bmp"})
_POINTS_PER_INCH = 72
_PIXEL_CAP_MARGIN = 0.99  # rounding the rendered size up must not push a page over the cap


class DocumentPageLoader:
    """Implements ``PageLoaderPort`` for PDFs and the common image formats."""

    # every collaborator is a number
    def __init__(
        self,
        *,
        max_file_bytes: int,
        max_pages: int,
        max_pixels: int,
        pdf_dpi: int,
        pdf_max_side_px: int = 10_000,
        pdf_picture_min_fraction: float = 1.1,  # above 1: never (the settings give 0.3)
        two_cards: TwoCards | None = None,  # None: a picture is never split
    ) -> None:
        self._max_file_bytes = max_file_bytes
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._pdf_dpi = pdf_dpi
        self._pdf_max_side_px = pdf_max_side_px
        self._pdf_picture_min_fraction = pdf_picture_min_fraction
        self._two_cards = two_cards

    def load_pages(self, content: bytes) -> tuple[PageImage, ...]:
        if not content or len(content) > self._max_file_bytes:
            message = "The file is empty or larger than this service accepts."
            raise FileRejectedError(message)
        kind = guess_extension(content)
        if kind == "pdf":
            return self._split(self._render_pdf(content))
        if kind in _IMAGE_TYPES:
            return self._split([(page, True) for page in self._decode_image(content)])
        message = "The file is not a PDF or a supported image (JPEG, PNG, WEBP, TIFF, BMP)."
        raise FileRejectedError(message)

    def _split(self, pages: Sequence[tuple[PageImage, bool]]) -> tuple[PageImage, ...]:
        """Each picture that holds a card's front and back becomes two pages, front first, as if the two sides had
        been sent as two files; a rendered PDF page (``False``: not a picture) is never split."""
        out: list[PageImage] = []
        for page, picture in pages:
            rgb = to_rgb_array(page)
            cards = find_two_cards(rgb, self._two_cards) if picture and self._two_cards else None
            if cards is None:
                out.append(page)
                continue
            out.extend(
                from_rgb_array(np.ascontiguousarray(rgb[top:bottom, left:right])) for left, top, right, bottom in cards
            )
        return tuple(replace(page, page_index=index) for index, page in enumerate(out))

    def _decode_image(self, content: bytes) -> tuple[PageImage, ...]:
        try:
            image = Image.open(io.BytesIO(content))
            if image.width * image.height > self._max_pixels:
                message = "The image has more pixels than this service accepts."
                raise FileRejectedError(message)
            frames = getattr(image, "n_frames", 1)
            if frames > self._max_pages:
                raise TooManyPagesError(f"The file has {frames} pages; at most {self._max_pages} are accepted.")
            pages: list[PageImage] = []
            for index in range(frames):
                image.seek(index)
                upright = ImageOps.exif_transpose(image.copy()).convert("RGB")
                pages.append(from_rgb_array(np.asarray(upright, dtype=np.uint8), page_index=index))
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as error:
            message = "The image could not be read."
            raise FileRejectedError(message) from error
        return tuple(pages)

    def _render_pdf(self, content: bytes) -> tuple[tuple[PageImage, bool], ...]:
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError as error:
            message = "The PDF could not be opened (it may be damaged or password protected)."
            raise FileRejectedError(message) from error
        try:
            if len(document) > self._max_pages:
                raise TooManyPagesError(f"The file has {len(document)} pages; at most {self._max_pages} are accepted.")
            return tuple(self._render_page(document, index) for index in range(len(document)))
        finally:
            document.close()

    def _render_page(self, document: pdfium.PdfDocument, index: int) -> tuple[PageImage, bool]:
        """The page as an image, and whether it is a photo or scan (its own picture) rather than a rendered page."""
        page = document[index]
        try:
            picture = self._picture(page)
            if picture is not None:  # a photo or scan saved as a PDF: its own pixels, no margins (pdf_pictures)
                return from_rgb_array(picture, page_index=index), True
            width_pt, height_pt = page.get_size()
            scale = self._pdf_dpi / _POINTS_PER_INCH
            fitting = (self._max_pixels / max(width_pt * height_pt, 1.0)) ** 0.5 * _PIXEL_CAP_MARGIN
            longest = self._pdf_max_side_px / max(width_pt, height_pt, 1.0)
            scale = min(scale, fitting, longest)  # huge pages, and pages over the long-side cap, are scaled down
            array: NDArray[np.uint8] = page.render(scale=scale).to_numpy()
        finally:
            page.close()
        return from_rgb_array(np.ascontiguousarray(array[:, :, 2::-1]), page_index=index), False  # pdfium gives BGR

    def _picture(self, page: pdfium.PdfPage) -> NDArray[np.uint8] | None:
        found = picture_of(page, min_fraction=self._pdf_picture_min_fraction)
        if found is None:
            return None
        try:
            image = found.get_bitmap(render=False).to_pil().convert("RGB")
        except (pdfium.PdfiumError, OSError, ValueError):
            return None  # an image format PDFium can't hand over: the page is rendered instead
        if image.width * image.height > self._max_pixels:
            return None
        return np.asarray(image, dtype=np.uint8)
