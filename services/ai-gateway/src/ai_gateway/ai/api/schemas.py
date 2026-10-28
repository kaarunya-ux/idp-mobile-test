"""Request and response bodies of the internal API (IDP-03 ch. 4.2). JSON is camelCase (RTR-06).

Every body names the account the call is for: it is used only for caps, never trusted for anything else, and the
``X-Account-Id`` header is ignored (IDP-03 ch. 4.2). Image keys are checked to be safe relative paths (FIL-06).
"""

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
    bucket: str = Field(pattern=_BUCKET)
    key: str = Field(min_length=1, max_length=1024)

    @field_validator("key")
    @classmethod
    def _safe(cls, key: str) -> str:
        if _UNSAFE_KEY.search(key):
            raise ValueError("the key must be a safe relative path")
        return key


class CallerBody(_Body):
    account_id: UUID
    priority: Priority = Priority.STANDARD
    plan: Plan | None = None  # which daily cap applies; when absent, PRIORITY means CORPORATE, STANDARD PERSONAL


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
