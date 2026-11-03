"""The synchronous extract's response body (ADR 0009). ``*Response`` stays in ``api`` (OBJ-01); JSON is camelCase,
as in the Java services (MSV-05)."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class TableBody(_Body):
    headers: list[str]
    rows: list[list[str]]


class ExtractedFieldBody(_Body):
    key: str = Field(description="lower snake_case, e.g. 'date_of_birth'; each key once")
    label: str = Field(description="as a person reads it, e.g. 'Date of Birth'")
    type: str = Field(description="string, number, date or table")
    value: str | TableBody | None = Field(description="as printed (Hindi in Devanagari); a table is {headers, rows}")
    confidence: float | None = Field(description="0 to 1; null when there is no value")
    status: str | None = Field(description="ACCEPTED or NEEDS_REVIEW for a known document's field; null otherwise")
    source: str | None = Field(description="OCR_AGREE, OCR_2_OF_3, AI or DIGITAL_TEXT for a known document's field")


class QuickExtractResponse(_Body):
    document_type: str | None = Field(description="a lower snake_case code such as 'pan_card'; null when unknown")
    document_confidence: float | None = Field(description="0 to 1")
    fields: list[ExtractedFieldBody] = Field(description="the document's fields, in order; [] when none")
    read_by: str = Field(description="OCR, OCR_AND_AI (the AI read the unsure fields' crops) or AI (unknown document)")
    model: str | None = Field(description="the AI model that read it, if any (provenance)")
    prompt_version: str | None
