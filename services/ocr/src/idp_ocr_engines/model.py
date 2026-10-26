"""The data model every engine returns: the ``OcrPage`` shared with the on-device OCR (IDP-03 ch. 2.8, IDP-04 ch. 3.2).

Plain frozen dataclasses with no third-party imports, so any process, including the slim API image, can import them
(ML-09). Boxes are fractions of the page with a top-left origin, as in the result schema (IDP-00 ch. 7.8).
"""

import math
from dataclasses import dataclass

from idp_ocr_engines.errors import InvalidGeometryError

_RGB_CHANNELS = 3
_TOLERANCE = 1e-6  # rounding slack when a box touches the page edge


@dataclass(frozen=True, slots=True)
class Box:
    """A rectangle ``[x, y, width, height]`` as fractions (0 to 1) of the page, origin at the top left."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        inside = 0 <= self.x <= 1 and 0 <= self.y <= 1
        sized = 0 < self.width <= 1 and 0 < self.height <= 1
        fits = self.x + self.width <= 1 + _TOLERANCE and self.y + self.height <= 1 + _TOLERANCE
        if not (inside and sized and fits):
            message = "A box must have positive size and lie inside the page (all values are fractions of the page)."
            raise InvalidGeometryError(message)

    @classmethod
    def from_pixels(
        cls, left: float, top: float, right: float, bottom: float, *, page_width: int, page_height: int
    ) -> "Box":
        """Build a box from pixel corners, clipped to the page. A box with no area left after clipping is an error."""
        clipped_left, clipped_right = max(0.0, left), min(float(page_width), right)
        clipped_top, clipped_bottom = max(0.0, top), min(float(page_height), bottom)
        return cls(
            x=clipped_left / page_width,
            y=clipped_top / page_height,
            width=(clipped_right - clipped_left) / page_width,
            height=(clipped_bottom - clipped_top) / page_height,
        )

    def to_pixels(self, *, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        """The ``(left, top, right, bottom)`` pixel corners on a page of the given size, rounded outwards."""
        left = int(self.x * page_width)
        top = int(self.y * page_height)
        right = min(page_width, math.ceil((self.x + self.width) * page_width - _TOLERANCE))
        bottom = min(page_height, math.ceil((self.y + self.height) * page_height - _TOLERANCE))
        return left, top, right, bottom


@dataclass(frozen=True, slots=True)
class PageImage:
    """One page as raw 8-bit RGB pixels, row by row (``width * height * 3`` bytes)."""

    width: int
    height: int
    rgb: bytes
    page_index: int = 0

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or len(self.rgb) != self.width * self.height * _RGB_CHANNELS:
            message = "A page image needs positive dimensions and exactly width x height x 3 bytes of RGB data."
            raise InvalidGeometryError(message)

    @property
    def pixel_count(self) -> int:
        """Total number of pixels, for the size cap (ML-06)."""
        return self.width * self.height


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrWord:
    """A piece of text the engine read, with where it is, how sure the engine is, and which line and block it is in."""

    text: str
    box: Box
    confidence: float
    line_id: int
    block_id: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            message = "Confidence is a score between 0 and 1."
            raise InvalidGeometryError(message)
        if self.line_id < 0 or self.block_id < 0:
            message = "Line and block ids are not negative."
            raise InvalidGeometryError(message)


@dataclass(frozen=True, slots=True)
class OcrPage:
    """Everything an engine read on one page, in reading order (top to bottom, then left to right)."""

    width: int
    height: int
    words: tuple[OcrWord, ...]

    @property
    def text(self) -> str:
        """All words joined line by line, for logging sizes and quick checks. Do not log it: it is document content."""
        lines: dict[int, list[str]] = {}
        for word in self.words:
            lines.setdefault(word.line_id, []).append(word.text)
        return "\n".join(" ".join(parts) for _, parts in sorted(lines.items()))


@dataclass(frozen=True, slots=True)
class OcrRegion:
    """The text read inside one box of a page (a field crop), as one string with the engine's confidence."""

    box: Box
    text: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            message = "Confidence is a score between 0 and 1."
            raise InvalidGeometryError(message)
