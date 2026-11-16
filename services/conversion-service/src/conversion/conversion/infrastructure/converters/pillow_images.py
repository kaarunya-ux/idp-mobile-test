"""Opening images safely with Pillow, and flattening transparency (FIL-05).

Damaged files and decompression bombs (tiny files that expand to billions of pixels) become domain errors
before any pixel data is decoded.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from conversion.conversion.domain.exceptions import LimitExceededError, UnreadableImageError

_WHITE = (255, 255, 255)


@contextmanager
def open_image(path: Path, *, max_pixels: int) -> Iterator[Image.Image]:
    """Open ``path``, refuse it above ``max_pixels``, decode it, and always close it."""
    try:
        image = Image.open(path)
    except Image.DecompressionBombError as exc:
        raise LimitExceededError(f"The image has more than {max_pixels:,} pixels.") from exc
    except FileNotFoundError:
        raise  # a missing input is our bug, not a problem with the user's file
    except OSError as exc:  # unknown format, or a header cut short (UnidentifiedImageError is an OSError too)
        raise UnreadableImageError from exc
    with image:
        width, height = image.size  # read from the header; no pixels decoded yet
        if width * height > max_pixels:
            raise LimitExceededError(f"The image has more than {max_pixels:,} pixels.")
        try:
            image.load()
        except OSError as exc:  # truncated or damaged pixel data
            raise UnreadableImageError from exc
        yield image


def has_transparency(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def flatten_on_white(image: Image.Image) -> Image.Image:
    """Lay a transparent image on a white background, as a viewer shows it. JPEG and PDF images can't keep alpha."""
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, _WHITE)
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background
