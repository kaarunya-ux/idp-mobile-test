"""Request and response bodies of the internal API (IDP-03 ch. 4.2). JSON is camelCase (RTR-06).

Every body names the account the call is for: it is used only for caps, never trusted for anything else, and the
``X-Account-Id`` header is ignored (IDP-03 ch. 4.2). Image keys are checked to be safe relative paths (FIL-06).
"""

import base64
import binascii
import json
import re
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from ai_gateway.ai.domain.models import Plan, Priority

_BUCKET = r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
_UNSAFE_KEY = re.compile(r"(^/)|(\.\.)|(\\)|[\x00-\x1f\x7f]")  # absolute, parent, backslash, control
_KEY = r"^[a-z][a-z0-9_]{0,63}$"
_DOCUMENT_TYPE = r"^[A-Z][A-Z0-9_]{0,63}$"
_MAX_CURRENT_RESULT_BYTES = 256 * 1024
_MAX_INLINE_BASE64 = 28 * 1024 * 1024  # 20 MiB of image as base64; the image store checks the real size limit

type FieldKey = Annotated[str, StringConstraints(pattern=_KEY)]
type DocumentTypeCode = Annotated[str, StringConstraints(pattern=_DOCUMENT_TYPE)]
type Reading = Annotated[str, StringConstraints(max_length=400)] | None
type Box = tuple[
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
]


class _Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)


class ImageRefBody(_Body):
    """``bucket`` and ``key`` of an image in storage, or ``data``: the image itself, base64 (the synchronous lane)."""

    bucket: str | None = Field(None, pattern=_BUCKET)
    key: str | None = Field(None, min_length=1, max_length=1024)
    data: str | None = Field(None, min_length=1, max_length=_MAX_INLINE_BASE64)

    @field_validator("key")
    @classmethod
    def _safe(cls, key: str | None) -> str | None:
        if key is not None and _UNSAFE_KEY.search(key):
            raise ValueError("the key must be a safe relative path")
        return key

    @field_validator("data")
    @classmethod
    def _base64(cls, data: str | None) -> str | None:
        if data is not None:
            try:
                base64.b64decode(data, validate=True)
            except binascii.Error as exc:
                raise ValueError("data must be base64") from exc
        return data

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        stored = self.bucket is not None and self.key is not None
        if stored == (self.data is not None) or (self.data is not None and (self.bucket or self.key)):
            raise ValueError("send either bucket and key, or data")
        return self


class CallerBody(_Body):
    account_id: UUID
    priority: Priority = Priority.STANDARD
    plan: Plan | None = None  # which daily cap applies; when absent, PRIORITY means CORPORATE, the others PERSONAL


class FieldBody(_Body):
    key: FieldKey
    label: str | None = Field(None, max_length=120)
    type: str | None = Field(None, max_length=32)
    rule: str | None = Field(None, max_length=300)
    readings: dict[str, Reading] = Field(default_factory=dict, max_length=3)  # OCR slot (A, B, C) -> text


class FieldReadRequest(CallerBody):
    """Batch form (``documentType``, ``pages``, ``fields``) or single form (``crop``, ``field``, ``readings``)."""

    document_type: DocumentTypeCode | None = None
    pages: list[ImageRefBody] | None = Field(None, min_length=1, max_length=2)  # a card's front and back
    fields: list[FieldBody] | None = Field(None, min_length=1, max_length=50)
    crop: ImageRefBody | None = None
    field: FieldBody | None = None
    readings: dict[str, Reading] = Field(default_factory=dict, max_length=3)

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        batch = self.pages is not None and self.fields is not None
        single = self.crop is not None and self.field is not None
        if batch == single or (batch and (self.crop or self.field)) or (single and (self.pages or self.fields)):
            raise ValueError("send either pages and fields, or crop and field")
        if batch and len({f.key for f in self.fields or ()}) != len(self.fields or ()):
            raise ValueError("field keys must be unique")
        return self


class TokensBody(_Body):
    input: int = Field(alias="in")
    output: int = Field(alias="out")


class _ProvenanceBody(_Body):
    model: str
    prompt_version: str
    latency_ms: int
    tokens: TokensBody


class FieldValueBody(_Body):
    key: str
    value: str | None


class FieldReadBatchResponse(_ProvenanceBody):
    fields: list[FieldValueBody]


class FieldReadSingleResponse(_ProvenanceBody):
    value: str | None
    confidence: float | None
    reason: str | None


class ClassifyRequest(CallerBody):
    page: ImageRefBody
    candidates: list[DocumentTypeCode] = Field(min_length=1, max_length=50)

    @field_validator("candidates")
    @classmethod
    def _unique(cls, candidates: list[str]) -> list[str]:
        if len(set(candidates)) != len(candidates):
            raise ValueError("candidates must be unique")
        return candidates


class ClassifyResponse(_ProvenanceBody):
    document_type: str | None
    confidence: float


class PageAnalysisRequest(CallerBody):
    pages: list[ImageRefBody] = Field(min_length=1, max_length=5)
    current_result: dict[str, Any] = Field(default_factory=dict)
    question: str | None = Field(None, min_length=1, max_length=500)

    @field_validator("current_result")
    @classmethod
    def _not_too_large(cls, current: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(current, separators=(",", ":"))) > _MAX_CURRENT_RESULT_BYTES:
            raise ValueError("currentResult is too large")
        return current


class AnalysedFieldBody(_Body):
    key: str
    label: str
    value: str | None
    confidence: float


class PageAnalysisResponse(_ProvenanceBody):
    fields: list[AnalysedFieldBody]
    summary: str
    warnings: list[str]


class OcrWordBody(_Body):
    text: str = Field(min_length=1, max_length=200)
    box: Box  # x, y, width, height as fractions of the page


class TemplateSuggestRequest(CallerBody):
    page: ImageRefBody
    ocr_words: list[OcrWordBody] = Field(default_factory=list, max_length=3000)


class SuggestedFieldBody(_Body):
    key: str
    label: str
    type: str
    box: tuple[float, float, float, float]
    rule: str | None


class SuggestedColumnBody(_Body):
    key: str
    label: str


class SuggestedTableBody(_Body):
    name: str
    box: tuple[float, float, float, float]
    columns: list[SuggestedColumnBody]


class TemplateSuggestResponse(_ProvenanceBody):
    fields: list[SuggestedFieldBody]
    tables: list[SuggestedTableBody]


class PageReadRequest(CallerBody):
    page: ImageRefBody


class PageAnalysisBody(_Body):
    n_blocks: int
    table_column_counts: list[int]


class PageBlockBody(_Body):
    """A text block (``text``, ``align``, ``size``, ``bold``) or a table (``hasHeader``, ``header``, ``rows``)."""

    kind: str
    text: str | None = None
    align: str | None = None
    size: str | None = None
    bold: bool | None = None
    has_header: bool | None = None
    n_cols: int | None = None
    header: list[str] | None = None
    rows: list[list[str]] | None = None


class PageReadResponse(_ProvenanceBody):
    analysis: PageAnalysisBody
    blocks: list[PageBlockBody]


class GenericReadRequest(CallerBody):
    """A document's page images: one, or a card's front and back (stacked and read as one document)."""

    pages: list[ImageRefBody] = Field(min_length=1, max_length=2)


class GenericTableBody(_Body):
    headers: list[str]
    rows: list[list[str]]


class GenericFieldBody(_Body):
    key: str
    label: str
    type: str  # string, number, date or table
    value: str | GenericTableBody | None
    confidence: float | None


class GenericReadResponse(_ProvenanceBody):
    document_type: str | None  # a lower snake_case code such as "aadhaar", or null when the model couldn't tell
    document_confidence: float | None
    fields: list[GenericFieldBody]


class OcrLineBody(_Body):
    """One line an OCR engine read: its number (unique on the page), its text and where it is."""

    n: int = Field(ge=0, le=100_000)
    text: str = Field(min_length=1, max_length=500)
    box: Box  # x, y, width, height as fractions of the page


class PageLayoutRequest(CallerBody):
    page: ImageRefBody | None = None  # without it, the model lays the page out from the lines and their boxes alone
    lines: list[OcrLineBody] = Field(min_length=1, max_length=2000)

    @field_validator("lines")
    @classmethod
    def _unique_numbers(cls, lines: list[OcrLineBody]) -> list[OcrLineBody]:
        if len({line.n for line in lines}) != len(lines):
            raise ValueError("line numbers must be unique")
        return lines


class LayoutBlockBody(_Body):
    """A text block (``kind``, ``lines``, ``align``, ``size``, ``bold``) or a table (``hasHeader``, ``rows``)."""

    kind: str
    lines: list[int] | None = None
    align: str | None = None
    size: str | None = None
    bold: bool | None = None
    has_header: bool | None = None
    rows: list[list[list[int]]] | None = None


class PageLayoutResponse(_ProvenanceBody):
    blocks: list[LayoutBlockBody]
