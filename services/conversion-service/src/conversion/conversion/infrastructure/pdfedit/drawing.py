"""Overlays drawn on top of a page: text, highlights, ink, images, watermarks and page numbers.

Each overlay is its own content stream after the page's, inside ``q``/``Q`` and moved into shown coordinates, so
it can't disturb the page and lands where the user saw it. Text uses the standard Helvetica font, which every
viewer has; it covers Latin letters only (Hindi text needs an embedded font, not built yet).
"""

import io
import math
import zlib
from pathlib import Path
from typing import Any, cast

import pikepdf
from pdfminer.fontmetrics import FONT_METRICS
from PIL import Image

from conversion.conversion.domain.edit_plan import (
    AnnotateText,
    Color,
    Highlight,
    Ink,
    PageNumbers,
    PlaceImage,
    Watermark,
)
from conversion.conversion.domain.exceptions import InvalidPlanError
from conversion.conversion.infrastructure.pdfedit.geometry import PageFrame

type Ops = list[tuple[list[object], str]]

_WIDTHS: dict[str, int] = FONT_METRICS["Helvetica"][1]
_DEFAULT_WIDTH = 556  # Helvetica's digit width, for characters the table lacks
_HIGHLIGHT_OPACITY = 0.4
_NUMBER_SIZE, _MARGIN = 10.0, 24.0
_WATERMARK_SPAN = 0.6  # of the page diagonal
WRAPPED = "/IdpWrapped"


def text_width(text: str, size: float) -> float:
    return sum(_WIDTHS.get(char, _DEFAULT_WIDTH) for char in text) * size / 1000


def annotate_text(pdf: pikepdf.Pdf, page: pikepdf.Page, op: AnnotateText) -> None:
    frame = PageFrame.of(page)
    x, y, w, h = frame.shown_rect(op.box)
    font = _font(page)
    lines = op.text.split("\n")
    ops: Ops = [*_clip(x, y, w, h), *_fill(op.color)]
    for number, line in enumerate(lines, start=1):
        baseline = y + h - op.font_size * 1.2 * number + op.font_size * 0.2
        ops += _text(font, op.font_size, x + 1, baseline, line)
    _draw(pdf, page, frame, ops)


def highlight(pdf: pikepdf.Pdf, page: pikepdf.Page, op: Highlight) -> None:
    frame = PageFrame.of(page)
    state = _alpha(page, _HIGHLIGHT_OPACITY, multiply=True)
    ops: Ops = [([pikepdf.Name(state)], "gs"), *_fill(op.color)]
    for box in op.boxes:
        ops += [(list(frame.shown_rect(box)), "re"), ([], "f")]
    _draw(pdf, page, frame, ops)


def ink(pdf: pikepdf.Pdf, page: pikepdf.Page, op: Ink) -> None:
    frame = PageFrame.of(page)
    width, height = frame.shown_size
    ops: Ops = [([op.width], "w"), ([1], "J"), ([1], "j"), (list(op.color), "RG")]
    for path in op.paths:
        for index, (fx, fy) in enumerate(path):
            ops.append(([fx * width, height - fy * height], "l" if index else "m"))
        ops.append(([], "S"))
    _draw(pdf, page, frame, ops)


def place_image(pdf: pikepdf.Pdf, page: pikepdf.Page, op: PlaceImage, image_path: Path) -> None:
    frame = PageFrame.of(page)
    x, y, w, h = frame.shown_rect(op.box)
    xobject, (pixels_w, pixels_h) = embed_image(pdf, image_path)
    name = page.add_resource(xobject, pikepdf.Name.XObject, prefix="IdpImg")
    scale = min(w / pixels_w, h / pixels_h)  # fit, keeping the image's shape, centred in the box
    shown_w, shown_h = pixels_w * scale, pixels_h * scale
    left, bottom = x + (w - shown_w) / 2, y + (h - shown_h) / 2
    _draw(pdf, page, frame, [([shown_w, 0, 0, shown_h, left, bottom], "cm"), ([name], "Do")])


def watermark(pdf: pikepdf.Pdf, page: pikepdf.Page, op: Watermark) -> None:
    frame = PageFrame.of(page)
    width, height = frame.shown_size
    size = min(200.0, _WATERMARK_SPAN * math.hypot(width, height) / max(text_width(op.text, 1), 1e-6))
    angle = math.radians(op.rotation)
    cos, sin = math.cos(angle), math.sin(angle)
    state, font = _alpha(page, op.opacity, multiply=False), _font(page)
    ops: Ops = [
        ([pikepdf.Name(state)], "gs"),
        ([0.5, 0.5, 0.5], "rg"),
        ([cos, sin, -sin, cos, width / 2, height / 2], "cm"),  # page centre, turned
        *_text(font, size, -text_width(op.text, size) / 2, -size / 3, op.text),
    ]
    _draw(pdf, page, frame, ops)


def page_number(pdf: pikepdf.Pdf, page: pikepdf.Page, op: PageNumbers, number: int, total: int) -> None:
    frame = PageFrame.of(page)
    width, height = frame.shown_size
    label = op.format.replace("{n}", str(number)).replace("{total}", str(total))
    label_width = text_width(label, _NUMBER_SIZE)
    column = op.position.value.split("_")[1]
    x = {"LEFT": _MARGIN, "CENTER": (width - label_width) / 2, "RIGHT": width - _MARGIN - label_width}[column]
    y = height - _MARGIN - _NUMBER_SIZE if op.position.value.startswith("TOP") else _MARGIN
    _draw(pdf, page, frame, [([0, 0, 0], "rg"), *_text(_font(page), _NUMBER_SIZE, x, y, label)])


def embed_image(pdf: pikepdf.Pdf, path: Path) -> tuple[pikepdf.Stream, tuple[int, int]]:
    """An image XObject: JPEG kept as JPEG, anything with transparency as RGB plus a soft mask."""
    with Image.open(path) as source:
        image = source.copy()
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        xobject = pdf.make_indirect(_raw_image(pdf, rgba.convert("RGB"), b"/DeviceRGB"))  # streams are indirect
        xobject[pikepdf.Name.SMask] = pdf.make_indirect(
            _raw_image(pdf, alpha, b"/DeviceGray")
        )  # streams must be indirect
        return xobject, image.size
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    stream = pikepdf.Stream(pdf, buffer.getvalue())
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    stream.Width, stream.Height = image.size
    stream.ColorSpace, stream.BitsPerComponent, stream.Filter = pikepdf.Name.DeviceRGB, 8, pikepdf.Name.DCTDecode
    return pdf.make_indirect(stream), image.size


def _raw_image(pdf: pikepdf.Pdf, image: Image.Image, color_space: bytes) -> pikepdf.Stream:
    stream = pikepdf.Stream(pdf, zlib.compress(image.tobytes()))
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    stream.Width, stream.Height = image.size
    stream.ColorSpace, stream.BitsPerComponent = pikepdf.Name(color_space.decode()), 8
    stream.Filter = pikepdf.Name.FlateDecode
    return stream


def encode(text: str) -> bytes:
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError:
        raise InvalidPlanError("Text drawn on a page can use Latin letters only for now.") from None


def _text(font: str, size: float, x: float, y: float, line: str) -> Ops:
    return [
        ([], "BT"),
        ([pikepdf.Name(font), size], "Tf"),
        ([x, y], "Td"),
        ([pikepdf.String(encode(line))], "Tj"),
        ([], "ET"),
    ]


def _fill(color: Color) -> Ops:
    return [(list(color), "rg")]


def _clip(x: float, y: float, w: float, h: float) -> Ops:
    return [([x, y, w, h], "re"), ([], "W"), ([], "n")]


def _font(page: pikepdf.Page) -> str:
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica,
        Encoding=pikepdf.Name.WinAnsiEncoding,
    )
    return str(page.add_resource(font, pikepdf.Name.Font, prefix="IdpHelv"))


def _alpha(page: pikepdf.Page, opacity: float, *, multiply: bool) -> str:
    state = pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=opacity, CA=opacity)
    if multiply:
        state.BM = pikepdf.Name.Multiply  # a highlighter: text under it stays readable
    return str(page.add_resource(state, pikepdf.Name.ExtGState, prefix="IdpGS"))


def _draw(pdf: pikepdf.Pdf, page: pikepdf.Page, frame: PageFrame, ops: Ops) -> None:
    """Close the page's own graphics state, then add the overlay in shown coordinates."""
    if WRAPPED not in page.obj:  # marked for this run only; the editor removes the mark before saving
        page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
        page.contents_add(pdf.make_stream(b"\nQ\n"))
        page.obj[WRAPPED] = True
    instructions: Ops = [([], "q"), (list(frame.to_user_space()), "cm"), *ops, ([], "Q")]
    # pikepdf turns plain numbers and Names into PDF objects itself; its type hints only list PDF objects
    content = pikepdf.unparse_content_stream(
        cast(Any, [(operands, pikepdf.Operator(operator)) for operands, operator in instructions])
    )
    page.contents_add(pdf.make_stream(content))
