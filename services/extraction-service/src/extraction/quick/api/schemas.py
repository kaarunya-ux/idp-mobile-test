"""The synchronous extract's response body (ADR 0009). ``*Response`` stays in ``api`` (OBJ-01); JSON is camelCase,
as in the Java services (MSV-05)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

_PERCENT_LIMIT = 100.5  # a box drawn to the page's edge may round a little past it


class _Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class TableBody(_Body):
    headers: list[str]
    rows: list[list[str]]


class FieldBoxBody(_Body):
    """Where a value is printed, in the same form a custom key's box is sent in: percent of the page as uploaded."""

    page: int = Field(description="1 for the first uploaded page (the front), 2 for the second (the back)")
    x: float = Field(description="left edge, percent of the page's width")
    y: float = Field(description="top edge, percent of the page's height")
    w: float = Field(description="width, percent of the page's width")
    h: float = Field(description="height, percent of the page's height")


class ExtractedFieldBody(_Body):
    id: str | None = Field(None, description="the custom route: the id the caller sent with this key; null otherwise")
    key: str = Field(description="lower snake_case, e.g. 'date_of_birth'; each key once")
    label: str = Field(description="as a person reads it, e.g. 'Date of Birth'")
    type: str = Field(description="string, number, date or table")
    value: str | TableBody | None = Field(description="as printed (Hindi in Devanagari); a table is {headers, rows}")
    confidence: float | None = Field(description="0 to 1; null when there is no value")
    status: str | None = Field(description="ACCEPTED or NEEDS_REVIEW for a known document's field; null otherwise")
    source: str | None = Field(description="OCR_AGREE, OCR_2_OF_3, AI or DIGITAL_TEXT for a known document's field")
    box: FieldBoxBody | None = Field(
        None, description="the generic read: where the value is printed; null when it can't be placed for sure"
    )


class QuickExtractResponse(_Body):
    document_type: str | None = Field(description="a lower snake_case code such as 'pan_card'; null when unknown")
    document_confidence: float | None = Field(description="0 to 1")
    fields: list[ExtractedFieldBody] = Field(description="the document's fields, in order; [] when none")
    read_by: str = Field(description="OCR, OCR_AND_AI (the AI read the unsure fields' crops) or AI (unknown document)")
    model: str | None = Field(description="the AI model that read it, if any (provenance)")
    prompt_version: str | None


class CustomBoxBody(BaseModel):
    """Where the user drew the box around the value on the sample: percent of the page (0 to 100), its top-left
    corner and its size, as the template designer stores it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(1, ge=1, le=1, description="1: the fields are read on the first page")
    x: float = Field(ge=0, lt=100, description="left edge, percent of the page's width")
    y: float = Field(ge=0, lt=100, description="top edge, percent of the page's height")
    w: float = Field(gt=0, le=100, description="width, percent of the page's width")
    h: float = Field(gt=0, le=100, description="height, percent of the page's height")

    @model_validator(mode="after")
    def _inside_the_page(self) -> "CustomBoxBody":
        if self.x + self.w > _PERCENT_LIMIT or self.y + self.h > _PERCENT_LIMIT:
            message = "the box must lie inside the page"
            raise ValueError(message)
        return self


class CustomFieldBody(BaseModel):
    """One key of a custom template, as the backend sends it in ``fields`` (ADR 0013)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = Field(
        None, min_length=1, max_length=128, description="optional, the caller's own id; sent back on this key's field"
    )
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$", description="lower snake_case, e.g. 'card_number'")
    label: str = Field(min_length=1, max_length=120, description="as printed on the document, e.g. 'Card No'")
    type: Literal["text", "date", "number"] = "text"
    box: CustomBoxBody | None = Field(None, description="drawn around the value; without it, found by its label")
