"""Image thumbnails with Pillow, for ``PREVIEW`` of a photo or scan (IDP-00 F-DC-05).

PDFs and Office files get their thumbnails from ``pdf_to_images`` with a size cap; an image has no such route
(JPEG -> JPEG is not a conversion), so it is shrunk here. The phone's rotation tag is applied, so the thumbnail
is upright, and transparency is laid on white because JPEG can't keep it.
"""

from pathlib import Path

from PIL import Image, ImageOps

from conversion.conversion.domain.values import ThumbnailSpec
from conversion.conversion.infrastructure.converters.pillow_images import flatten_on_white, has_transparency, open_image


class PillowThumbnailer:
    def __init__(self, *, max_pixels: int) -> None:
        self._max_pixels = max_pixels

    def thumbnail(self, source: Path, output: Path, spec: ThumbnailSpec) -> None:
        with open_image(source, max_pixels=self._max_pixels) as image:
            upright = ImageOps.exif_transpose(image)
        if has_transparency(upright):
            upright = flatten_on_white(upright)
        elif upright.mode not in ("RGB", "L"):
            upright = upright.convert("RGB")
        upright.thumbnail((spec.max_side_px, spec.max_side_px), Image.Resampling.LANCZOS)  # never enlarges
        upright.save(output, format="JPEG", quality=spec.jpeg_quality, optimize=True)
