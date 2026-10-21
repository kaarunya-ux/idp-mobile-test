"""Conversion options from the job request, checked as business rules (IDP-02 ch. 3.6, DOM-02).

document-service validates options before dispatching a job; these checks are the service's own guarantee
that a bad value becomes a clear ``CNV-422-OPTIONS-001`` instead of a crash.
"""

import re
from dataclasses import dataclass
from typing import Self

from conversion.conversion.domain.exceptions import InvalidOptionError, PageRangeOutsideDocumentError

MIN_DPI, MAX_DPI, DEFAULT_DPI = 72, 600, 150
MIN_JPEG_QUALITY, MAX_JPEG_QUALITY, DEFAULT_JPEG_QUALITY = 1, 95, 90
MAX_PAGE_RANGE_LENGTH = 200  # characters; bounds the work a single option can ask for (SCH-01)
_SPAN = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")


@dataclass(frozen=True, slots=True)
class PageRange:
    """1-based pages to include, e.g. ``1-3,5`` means pages 1, 2, 3 and 5, in that order."""

    spans: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not self.spans:
            raise InvalidOptionError("pageRange", "must name at least one page")
        for first, last in self.spans:
            if first < 1 or last < first:
                raise InvalidOptionError("pageRange", f"has an impossible span {first}-{last}")

    @classmethod
    def parse(cls, text: str) -> Self:
        """Read the ``pageRange`` option: comma-separated pages (``4``) or spans (``2-7``)."""
        if len(text) > MAX_PAGE_RANGE_LENGTH:
            raise InvalidOptionError("pageRange", f"is longer than {MAX_PAGE_RANGE_LENGTH} characters")
        spans: list[tuple[int, int]] = []
        for part in text.replace(" ", "").split(","):
            match = _SPAN.match(part)
            if match is None:
                raise InvalidOptionError("pageRange", f"has '{part}', which is not a page or a span like 2-5")
            first = int(match.group(1))
            spans.append((first, int(match.group(2) or first)))
        return cls(tuple(spans))

    def select(self, page_count: int) -> list[int]:
        """0-based indexes of the requested pages that exist, in request order, each page once."""
        chosen: dict[int, None] = {}  # a dict keeps insertion order and drops repeats
        for first, last in self.spans:
            for page in range(first, min(last, page_count) + 1):
                chosen.setdefault(page - 1, None)
        if not chosen:
            raise PageRangeOutsideDocumentError(page_count)
        return list(chosen)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversionOptions:
    """The options that change how a converter works. Converters ignore the ones that don't apply to them."""

    page_range: PageRange | None = None
    dpi: int = DEFAULT_DPI
    jpeg_quality: int = DEFAULT_JPEG_QUALITY

    def __post_init__(self) -> None:
        if not MIN_DPI <= self.dpi <= MAX_DPI:
            raise InvalidOptionError("dpi", f"must be between {MIN_DPI} and {MAX_DPI}")
        if not MIN_JPEG_QUALITY <= self.jpeg_quality <= MAX_JPEG_QUALITY:
            raise InvalidOptionError("quality", f"must be between {MIN_JPEG_QUALITY} and {MAX_JPEG_QUALITY}")
