"""Conversion options from the job request, checked as business rules (IDP-02 ch. 3.6, DOM-02).

document-service validates options before dispatching a job; these checks are the service's own guarantee
that a bad value becomes a clear ``CNV-422-OPTIONS-001`` instead of a crash.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from conversion.conversion.domain.exceptions import InvalidOptionError, PageRangeOutsideDocumentError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.route_planner import OcrMode
from conversion.conversion.domain.values import OutputMode

MIN_DPI, MAX_DPI, DEFAULT_DPI = 72, 600, 150
MIN_SIDE_PX, MAX_SIDE_PX = 32, 20_000
MIN_JPEG_QUALITY, MAX_JPEG_QUALITY, DEFAULT_JPEG_QUALITY = 1, 95, 90
MAX_PAGE_RANGE_LENGTH = 200  # characters; bounds the work a single option can ask for (SCH-01)
MAX_SHEET_NAME_LENGTH = 31  # Excel's own limit
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
    sheet: str | None = None  # which sheet to export from a workbook; the first sheet when not given
    max_side_px: int | None = None  # set by PREVIEW, never by Java: rendered pages fit this square

    def __post_init__(self) -> None:
        if self.sheet is not None and not 1 <= len(self.sheet) <= MAX_SHEET_NAME_LENGTH:
            raise InvalidOptionError("sheet", f"must be 1 to {MAX_SHEET_NAME_LENGTH} characters")
        if not MIN_DPI <= self.dpi <= MAX_DPI:
            raise InvalidOptionError("dpi", f"must be between {MIN_DPI} and {MAX_DPI}")
        if self.max_side_px is not None and not MIN_SIDE_PX <= self.max_side_px <= MAX_SIDE_PX:
            raise InvalidOptionError("max_side_px", f"must be between {MIN_SIDE_PX} and {MAX_SIDE_PX}")
        if not MIN_JPEG_QUALITY <= self.jpeg_quality <= MAX_JPEG_QUALITY:
            raise InvalidOptionError("quality", f"must be between {MIN_JPEG_QUALITY} and {MAX_JPEG_QUALITY}")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobOptions:
    """Everything a ``CONVERSION`` job's options say, typed (IDP-02 ch. 3.6)."""

    conversion: ConversionOptions
    ocr: OcrMode
    output: OutputMode


def parse_job_options(raw: Mapping[str, object]) -> JobOptions:
    """Read the options JSON Java sends. Unknown keys are ignored (MSG-06); known ones must have the right type.

    ``mode`` (EQUAL_COPY / EXTRACTED_COPY) only matters once OCR exists, so it is accepted and not used yet.
    """
    page_range = _text(raw, "pageRange")
    dpi = _number(raw, "dpi")
    quality = _number(raw, "quality")
    return JobOptions(
        conversion=ConversionOptions(
            page_range=PageRange.parse(page_range) if page_range is not None else None,
            dpi=dpi if dpi is not None else DEFAULT_DPI,
            jpeg_quality=quality if quality is not None else DEFAULT_JPEG_QUALITY,
            sheet=_text(raw, "sheet"),
        ),
        ocr=_choice(raw, "ocr", OcrMode, OcrMode.AUTO),
        output=_choice(raw, "output", OutputMode, OutputMode.PER_PAGE),
    )


def resolve_target(target: str, image_format: object = None) -> Format:
    """The format a job asks for. ``IMAGE`` plus the ``imageFormat`` option means PNG (the default) or JPEG."""
    name = target.strip().upper()
    if name == "IMAGE":
        name = str(image_format or Format.PNG).upper()
    if name == "JPG":
        name = Format.JPEG
    try:
        return Format(name)
    except ValueError:
        raise InvalidOptionError("target", f"'{target}' is not a format this service writes") from None


def _text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidOptionError(key, "must be text")
    return value


def _number(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):  # JSON true is a bool, which is an int in Python
        raise InvalidOptionError(key, "must be a whole number")
    return value


def _choice[E: StrEnum](raw: Mapping[str, object], key: str, kind: type[E], default: E) -> E:
    value = raw.get(key)
    if value is None:
        return default
    try:
        return kind(str(value).upper())
    except ValueError:
        allowed = ", ".join(kind)
        raise InvalidOptionError(key, f"must be one of {allowed}") from None
