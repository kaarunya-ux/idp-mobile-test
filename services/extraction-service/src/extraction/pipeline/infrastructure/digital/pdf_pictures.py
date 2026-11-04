"""A PDF page that is one picture: a photo or scan of a card, saved or printed as a PDF.

Such a page is read from the picture itself, at its own pixels: rendering the whole page at 300 DPI made a 768 x 972 px
card photo on a letter page a 2550 x 3301 px image, eleven times the pixels, with white margins around the card, so OCR
was slow and found the card's fields nowhere near where the template expects them. Text on a picture page is a header
or the hidden text layer of a scan, not the document, so it is not read as a digital PDF's text either.

A page is a picture page when its largest image covers at least ``min_fraction`` of it (a real e-PAN or e-Aadhaar has
only a small photo and QR code) and is placed upright (not rotated or mirrored).
"""

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

_EPSILON = 1e-6


def picture_of(page: pdfium.PdfPage, *, min_fraction: float) -> pdfium.PdfImage | None:
    """The page's picture, or None when the page is not one picture."""
    width, height = page.get_size()
    area = max(width * height, _EPSILON)
    best: pdfium.PdfImage | None = None
    best_fraction = 0.0
    for found in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
        left, bottom, right, top = found.get_bounds()
        fraction = (right - left) * (top - bottom) / area
        if fraction > best_fraction:
            best, best_fraction = found, fraction
    if best is None or best_fraction < min_fraction or not _upright(best):
        return None
    return best


def _upright(image: pdfium.PdfImage) -> bool:
    """Placed without rotation or mirroring, so its own pixels are the page's view of it."""
    a, b, c, d, _, _ = (float(v) for v in image.get_matrix().get())  # pypdfium2 has no types: numbers here
    return abs(b) < _EPSILON and abs(c) < _EPSILON and a > 0 and d > 0
