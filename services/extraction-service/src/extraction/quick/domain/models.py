"""The synchronous extract (ADR 0009): who asks, the pages as stored for ai-gateway, and what was read."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from extraction.core.permissions import Plan
from extraction.quick.domain.placed import PlacedBox


class FileKind(StrEnum):
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"


@dataclass(frozen=True, slots=True, kw_only=True)
class QuickCaller:
    """The account the call is for (ai-gateway counts it against that account's daily AI calls) and its plan."""

    account_id: UUID
    plan: Plan


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadLimits:
    max_file_bytes: int
    max_total_bytes: int


class GenericMode(StrEnum):
    """How the generic read works (ADR 0015)."""

    PICTURE = "picture"  # the model reads the page image and writes every value
    LINES = "lines"  # OCR reads the lines; the model only names which lines hold which field


class ReadBy(StrEnum):
    OCR = "OCR"  # the two engines settled every field
    OCR_AND_AI = "OCR_AND_AI"  # the AI read the crops of the fields they left unsure
    AI = "AI"  # no template matched: the AI read the whole document (generic-read)


@dataclass(frozen=True, slots=True, kw_only=True)
class StagedFile:
    """A prepared page in the derived bucket, for as long as the call lasts: ai-gateway reads images by key."""

    bucket: str
    key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Table:
    headers: Sequence[str]
    rows: Sequence[Sequence[str]]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedField:
    key: str
    label: str
    type: str  # string, number, date or table
    value: str | Table | None
    confidence: float | None  # 0 to 1; None when there is no value
    status: str | None = None  # ACCEPTED or NEEDS_REVIEW for a template's field; None for the generic read
    source: str | None = None  # OCR_AGREE, OCR_2_OF_3, AI or DIGITAL_TEXT for a template's field
    box: PlacedBox | None = None  # the generic read: where the value is printed on the pages as uploaded


@dataclass(frozen=True, slots=True, kw_only=True)
class QuickResult:
    """A document's type and its fields, with who read them and the model and prompt, if any (ML-04)."""

    document_type: str | None  # a lower snake_case code such as "pan_card"; None when nobody could tell
    document_confidence: float | None
    fields: Sequence[ExtractedField]
    read_by: ReadBy
    model_id: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineOutcome:
    """What OCR made of the document: the result when a template matched (None when none did), and the prepared
    pages as stored, for the generic read."""

    result: QuickResult | None
    pages: Sequence[StagedFile]
