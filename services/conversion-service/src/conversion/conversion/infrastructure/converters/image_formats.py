"""PNG <-> JPEG with Pillow (IDP-03 ch. 3.4: ``image_formats``).

The phone's rotation tag is applied, and the colour profile and DPI are kept. Ported from
``services/conversions/python_scripts/jpg_to_png.py`` (Sumukh, Karunya).
"""

from numbers import Real
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pillow_images import flatten_on_white, has_transparency, open_image

_EXIF_ORIENTATION = 0x0112
_QUARTER_TURNS = {5, 6, 7, 8}  # EXIF orientations that swap width and height
_SAVE_FORMAT = {Representation.PNG: "PNG", Representation.JPEG: "JPEG"}
_SUFFIX = {Representation.PNG: ".png", Representation.JPEG: ".jpg"}


class PillowImageFormatConverter:
    """Converts each input image to the step's target format, one output per input."""

    def __init__(self, *, max_pixels: int) -> None:
        self._max_pixels = max_pixels

    @property
    def converter(self) -> Converter:
        return Converter.IMAGE_FORMATS

    def convert(self, request: StepRequest) -> StepResult:
        outputs = tuple(
            self._convert_one(source, request.output_dir, request.step.target, request.options)
            for source in request.inputs
        )
        return StepResult(outputs=outputs, page_count=len(outputs))

    def _convert_one(self, source: Path, output_dir: Path, target: Representation, options: ConversionOptions) -> Path:
        with open_image(source, max_pixels=self._max_pixels) as image:
            orientation = int(image.getexif().get(_EXIF_ORIENTATION, 1) or 1)
            dpi = _usable_dpi(image.info.get("dpi"))
            icc_profile = image.info.get("icc_profile")
            upright = ImageOps.exif_transpose(image)  # applies the tag; the copy carries no tag
        if dpi is not None and orientation in _QUARTER_TURNS:
            dpi = (dpi[1], dpi[0])  # the picture was turned a quarter-turn
        if upright.mode == "CMYK":
            upright, icc_profile = upright.convert("RGB"), None  # a CMYK profile doesn't describe RGB pixels
        if target is Representation.JPEG:
            upright = _for_jpeg(upright)
        output = output_dir / f"{source.stem}{_SUFFIX[target]}"
        upright.save(output, format=_SAVE_FORMAT[target], **_save_options(target, options, dpi, icc_profile))
        return output


def _for_jpeg(image: Image.Image) -> Image.Image:
    if has_transparency(image):
        return flatten_on_white(image)
    return image if image.mode in ("RGB", "L") else image.convert("RGB")


def _usable_dpi(value: object) -> tuple[float, float] | None:
    """Pillow reports DPI as an (x, y) pair of numbers, sometimes as fractions; anything else is ignored."""
    match value:
        case (Real() as x, Real() as y):
            pair = (float(x), float(y))
            return pair if min(pair) >= 1 else None
    return None


def _save_options(
    target: Representation, options: ConversionOptions, dpi: tuple[float, float] | None, icc_profile: object
) -> dict[str, Any]:  # Any: Pillow's save() takes format-specific keyword arguments
    save: dict[str, Any] = {}
    if dpi is not None:
        save["dpi"] = dpi
    if isinstance(icc_profile, bytes):
        save["icc_profile"] = icc_profile
    if target is Representation.JPEG:
        save["quality"] = options.jpeg_quality
    return save
