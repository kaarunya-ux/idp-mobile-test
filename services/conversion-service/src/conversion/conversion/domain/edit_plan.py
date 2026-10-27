"""The PDF edit plan (IDP-03 ch. 3.5), read from JSON into checked values.

The editors build the plan and send it once; the worker applies it in one pass: assemble the pages, apply the
operations in order, then split. ``edit_plan_parser`` reads it; anything malformed is ``CNV-422-PLAN-001``
before any PDF is opened.

Conventions, from the schema example:

- ``pages[].source.page`` counts from 1, as people do; ``operations[].page`` is an **output** page index from 0.
- A ``box`` is ``[x, y, width, height]`` as fractions of the page as shown (rotation applied), from the top left.
- Passwords stay encrypted in the plan; the worker decrypts them just before use and never stores or logs them.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from conversion.conversion.domain.options import PageRange

SELF = "self"  # documentId of the job's own input
MAX_OUTPUT_PAGES, MAX_OPERATIONS = 1000, 500

type Box = tuple[float, float, float, float]
type Color = tuple[float, float, float]  # 0-1 RGB


class PageSize(StrEnum):
    A4 = "A4"
    LETTER = "LETTER"
    LEGAL = "LEGAL"


PAGE_SIZES_PT: Mapping[PageSize, tuple[float, float]] = {
    PageSize.A4: (595.28, 841.89),
    PageSize.LETTER: (612.0, 792.0),
    PageSize.LEGAL: (612.0, 1008.0),
}


class Position(StrEnum):
    TOP_LEFT = "TOP_LEFT"
    TOP_CENTER = "TOP_CENTER"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_CENTER = "BOTTOM_CENTER"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"


class CompressPreset(StrEnum):
    LOSSLESS = "LOSSLESS"  # streams recompressed; images untouched
    BALANCED = "BALANCED"  # + large images re-encoded as JPEG 75
    STRONG = "STRONG"  # + images scaled to 1,600 px and JPEG 50


class SplitMode(StrEnum):
    RANGES = "RANGES"
    EVERY = "EVERY"
    BOOKMARKS = "BOOKMARKS"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourcePage:
    document_id: str
    page: int  # from 1
    rotate: int  # absolute: 0, 90, 180 or 270


@dataclass(frozen=True, slots=True, kw_only=True)
class BlankPage:
    size: PageSize
    landscape: bool


type PageSpec = SourcePage | BlankPage


# --- Operations -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AnnotateText:
    page: int
    box: Box
    text: str
    font_size: float
    color: Color


@dataclass(frozen=True, slots=True, kw_only=True)
class Highlight:
    page: int
    boxes: tuple[Box, ...]
    color: Color


@dataclass(frozen=True, slots=True, kw_only=True)
class Ink:
    page: int
    paths: tuple[tuple[tuple[float, float], ...], ...]
    width: float
    color: Color


@dataclass(frozen=True, slots=True, kw_only=True)
class PlaceImage:
    """``SIGNATURE`` and ``INSERT_IMAGE``: an image from storage, fitted into the box."""

    page: int
    box: Box
    image_key: str  # "<bucket>/<key>"


@dataclass(frozen=True, slots=True, kw_only=True)
class Watermark:
    text: str
    opacity: float
    rotation: float
    pages: PageRange | None  # None: every page


@dataclass(frozen=True, slots=True, kw_only=True)
class PageNumbers:
    position: Position
    format: str  # "Page {n} of {total}"
    start_at: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RedactArea:
    page: int
    boxes: tuple[Box, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RedactText:
    query: str
    match_case: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class FillForm:
    fields: Mapping[str, str]
    flatten: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplaceText:
    page: int
    find: str
    replace: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Metadata:
    changes: Mapping[str, str | None]  # title / author / subject / keywords; None removes the entry


@dataclass(frozen=True, slots=True, kw_only=True)
class Protect:
    user_password: str  # encrypted until the worker applies it
    owner_password: str
    allow_print: bool
    allow_copy: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Compress:
    preset: CompressPreset


type Operation = (
    AnnotateText
    | Highlight
    | Ink
    | PlaceImage
    | Watermark
    | PageNumbers
    | RedactArea
    | RedactText
    | FillForm
    | ReplaceText
    | Metadata
    | Protect
    | Compress
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Split:
    mode: SplitMode
    ranges: tuple[PageRange, ...] = ()
    every: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class EditPlan:
    pages: tuple[PageSpec, ...] | None  # None: the document's pages, unchanged
    operations: tuple[Operation, ...]
    split: Split | None
    password: str | None  # encrypted; opens the job's own PDF

    @property
    def document_ids(self) -> frozenset[str]:
        """Every document the pages come from; ``self`` always."""
        return frozenset({SELF} | {p.document_id for p in self.pages or () if isinstance(p, SourcePage)})

    @property
    def image_keys(self) -> frozenset[str]:
        return frozenset(op.image_key for op in self.operations if isinstance(op, PlaceImage))
