"""True redaction (IDP-03 ch. 3.5): what is under a box is gone from the file, not just covered.

A page with something to redact is drawn as a picture by PDFium, the boxes are painted black, and the page is
replaced by that picture. Nothing of the original page survives on it: no text, no hidden layers, no images
underneath. The cost is that the page's other text can no longer be selected. After every edit, each redacted
search term is looked for again in the finished file; finding one is a failure, never a warning.
"""

import io
from collections.abc import Iterable, Mapping, Sequence

import pikepdf
import pypdfium2 as pdfium
from PIL import ImageDraw

from conversion.conversion.domain.edit_plan import Box
from conversion.conversion.infrastructure.pdfedit.geometry import PageFrame

POINTS_PER_INCH = 72
_PAD_PT = 1.0  # a hair around each found word, so anti-aliased edges don't survive
_JPEG_QUALITY = 90


class RedactionNotVerifiedError(RuntimeError):
    """A redacted term is still in the output. A bug, never a user error: the item fails and is retried."""


def find_text(pdf: pikepdf.Pdf, query: str, *, match_case: bool) -> dict[int, list[Box]]:
    """Where ``query`` appears, as plan boxes per page index."""
    found: dict[int, list[Box]] = {}
    with _as_pdfium(pdf) as document:
        for index in range(len(document)):
            boxes = list(_matches(document, index, query, match_case=match_case, frame=PageFrame.of(pdf.pages[index])))
            if boxes:
                found[index] = boxes
    return found


def redact(pdf: pikepdf.Pdf, boxes: Mapping[int, Sequence[Box]], *, dpi: int) -> None:
    """Replace each listed page with a picture of it, the boxes painted black."""
    with _as_pdfium(pdf) as document:
        pictures = {index: _picture(document, index, page_boxes, dpi) for index, page_boxes in boxes.items()}
    for index, (image_bytes, size_px, size_pt) in pictures.items():
        _replace_page(pdf, index, image_bytes, size_px, size_pt)


def count_matches(data: bytes, queries: Iterable[tuple[str, bool]]) -> int:
    document = pdfium.PdfDocument(data)
    try:
        total = 0
        for query, match_case in queries:
            for index in range(len(document)):
                total += sum(1 for _ in _hits(document, index, query, match_case=match_case))
        return total
    finally:
        document.close()


def verify(data: bytes, queries: Sequence[tuple[str, bool]]) -> None:
    if queries and count_matches(data, queries):
        raise RedactionNotVerifiedError("A redacted search term is still in the output.")


def _as_pdfium(pdf: pikepdf.Pdf) -> "_PdfiumCopy":
    return _PdfiumCopy(pdf)


class _PdfiumCopy:
    """The pikepdf document as it is now, opened in PDFium (which can draw pages and find text)."""

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        buffer = io.BytesIO()
        pdf.save(buffer)
        self._document = pdfium.PdfDocument(buffer.getvalue())

    def __enter__(self) -> pdfium.PdfDocument:
        return self._document

    def __exit__(self, *_: object) -> None:
        self._document.close()


def _hits(
    document: pdfium.PdfDocument, index: int, query: str, *, match_case: bool
) -> Iterable[list[tuple[float, ...]]]:
    page = document[index]
    text_page = page.get_textpage()
    try:
        searcher = text_page.search(query, match_case=match_case)
        while (hit := searcher.get_next()) is not None:
            start, count = hit
            yield [text_page.get_rect(i) for i in range(text_page.count_rects(start, count))]
    finally:
        text_page.close()
        page.close()


def _matches(
    document: pdfium.PdfDocument, index: int, query: str, *, match_case: bool, frame: PageFrame
) -> Iterable[Box]:
    for rects in _hits(document, index, query, match_case=match_case):
        for left, bottom, right, top in rects:
            yield frame.box_from_user_rect(left - _PAD_PT, bottom - _PAD_PT, right + _PAD_PT, top + _PAD_PT)


def _picture(
    document: pdfium.PdfDocument, index: int, boxes: Sequence[Box], dpi: int
) -> tuple[bytes, tuple[int, int], tuple[float, float]]:
    page = document[index]
    try:
        size_pt = page.get_size()  # as shown: PDFium applies the page's rotation
        image = page.render(scale=dpi / POINTS_PER_INCH).to_pil().convert("RGB")  # untyped library -> typed here
    finally:
        page.close()
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for x, y, w, h in boxes:
        draw.rectangle(
            (int(x * width), int(y * height), int((x + w) * width + 1), int((y + h) * height + 1)), fill=(0, 0, 0)
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue(), image.size, (float(size_pt[0]), float(size_pt[1]))


def _replace_page(
    pdf: pikepdf.Pdf, index: int, image_bytes: bytes, size_px: tuple[int, int], size_pt: tuple[float, float]
) -> None:
    image = pikepdf.Stream(pdf, image_bytes)
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height = size_px
    image.ColorSpace, image.BitsPerComponent, image.Filter = pikepdf.Name.DeviceRGB, 8, pikepdf.Name.DCTDecode
    width, height = size_pt
    content = f"q {width:.4f} 0 0 {height:.4f} 0 0 cm /Picture Do Q".encode()
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, width, height],
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Picture=image)),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.insert(index, pikepdf.Page(pdf.make_indirect(page)))
    del pdf.pages[index + 1]
