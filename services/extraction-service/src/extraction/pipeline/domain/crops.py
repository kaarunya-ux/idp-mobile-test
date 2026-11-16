"""The crop of one field, for the AI step: the area the OCR engines read the field in, with a margin (stage 7).

Pure: the page is raw RGB bytes, so the crop is cut row by row without an imaging library (the domain imports none).
"""

from idp_ocr_engines.model import Box, PageImage

_CHANNELS = 3


def padded(box: Box, *, padding: float) -> Box:
    """The box grown on every side by ``padding`` times its height (a field is a line: its height is the scale that
    keeps the letters whole), kept inside the page."""
    margin = box.height * padding
    left, top = max(0.0, box.x - margin), max(0.0, box.y - margin)
    right, bottom = min(1.0, box.x + box.width + margin), min(1.0, box.y + box.height + margin)
    return Box(x=left, y=top, width=right - left, height=bottom - top)


def crop_page(page: PageImage, box: Box) -> PageImage:
    """The part of the page inside ``box``."""
    left, top, right, bottom = box.to_pixels(page_width=page.width, page_height=page.height)
    width = max(1, right - left)
    rows = []
    for y in range(top, max(top + 1, bottom)):
        start = (y * page.width + left) * _CHANNELS
        rows.append(page.rgb[start : start + width * _CHANNELS])
    return PageImage(width=width, height=len(rows), rgb=b"".join(rows), page_index=page.page_index)
