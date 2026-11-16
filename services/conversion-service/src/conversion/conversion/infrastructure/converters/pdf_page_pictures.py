"""The pictures on a digital PDF page, for the document model (PDF -> Word).

Each is drawn from the page at ``_DPI`` rather than taken from the file: PDF images come in many encodings, masks and
colour spaces, and drawing them gives a plain image whatever they are. Marks too small to be a picture (a bullet, a
rule) are left out, and so is one that fills the page: a background, or the scan behind a hidden text layer.
"""

import io

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import structlog
from PIL import Image

from conversion.conversion.domain.document_model import Picture

log = structlog.get_logger(__name__)

_DPI = 150
_POINTS_PER_INCH = 72
_MIN_SIDE_PT = 24.0  # a third of an inch: anything smaller is a mark, not a picture
_MAX_PAGE_SHARE = 0.9  # a picture covering this share of the page is its background
_JPEG_QUALITY = 90


def pictures_on(page: pdfium.PdfPage) -> list[tuple[float, Picture]]:
    """Each picture with its top edge, measured from the top of the page in points, in the order they are drawn."""
    if page.get_rotation() != 0:  # the images' positions are in the unrotated page's coordinates
        log.info("pdf_pictures_skipped_on_rotated_page")
        return []
    width, height = page.get_size()
    found: list[tuple[float, Picture]] = []
    for image in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
        left, bottom, right, top = (float(side) for side in image.get_bounds())
        left, bottom, right, top = max(left, 0.0), max(bottom, 0.0), min(right, width), min(top, height)
        shown_width, shown_height = right - left, top - bottom
        if (
            min(shown_width, shown_height) < _MIN_SIDE_PT
            or shown_width * shown_height >= width * height * _MAX_PAGE_SHARE
        ):
            continue
        drawn: Image.Image = page.render(
            scale=_DPI / _POINTS_PER_INCH, crop=(left, bottom, width - right, height - top)
        ).to_pil()
        found.append((height - top, Picture(data=_jpeg(drawn), width_pt=shown_width)))
    return found


def _jpeg(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.convert("RGB").save(stream, format="JPEG", quality=_JPEG_QUALITY)
    return stream.getvalue()
