"""The shape each task's answer must have (LLM-03): a Pydantic model to validate it, and the JSON schema sent as
``response_format`` so a host that enforces schemas can only answer in this shape."""

import re
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_gateway.ai.domain.answers import parse_json
from ai_gateway.ai.domain.exceptions import AnswerShapeError

_KEY = r"^[a-z][a-z0-9_]{0,63}$"
_FIELD_TYPES = r"^(TEXT|NAME|ID|DATE|NUMBER|ADDRESS)$"
_VALUE_LENGTH = 400
_MAX_FIELDS = 100


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)  # an extra key from a chatty model is dropped, not fatal


class SingleFieldAnswer(_Answer):
    value: str | None = Field(max_length=_VALUE_LENGTH)
    confidence: float = Field(ge=0, le=1)
    reason: str | None = Field(None, max_length=300)


class ClassifyAnswer(_Answer):
    document_type: str | None = Field(alias="documentType", max_length=64)
    confidence: float = Field(ge=0, le=1)


class AnalysedField(_Answer):
    key: str = Field(pattern=_KEY)
    label: str = Field(max_length=120)
    value: str | None = Field(max_length=_VALUE_LENGTH)
    confidence: float = Field(ge=0, le=1)


class PageAnalysisAnswer(_Answer):
    fields: list[AnalysedField] = Field(max_length=_MAX_FIELDS)
    summary: str = Field(max_length=2000)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class SuggestedField(_Answer):
    key: str = Field(pattern=_KEY)
    label: str = Field(max_length=120)
    type: str = Field(pattern=_FIELD_TYPES)
    box: tuple[float, float, float, float]  # x, y, width, height as fractions of the page
    rule: str | None = Field(None, max_length=300)


class SuggestedColumn(_Answer):
    key: str = Field(pattern=_KEY)
    label: str = Field(max_length=120)


class SuggestedTable(_Answer):
    name: str = Field(max_length=120)
    box: tuple[float, float, float, float]
    columns: list[SuggestedColumn] = Field(max_length=50)


class TemplateSuggestAnswer(_Answer):
    fields: list[SuggestedField] = Field(max_length=_MAX_FIELDS)
    tables: list[SuggestedTable] = Field(default_factory=list, max_length=10)


def reader[M: BaseModel](model: type[M]) -> Callable[[str], M]:
    """A function that turns an answer text into ``model``, or raises ``AnswerShapeError``."""

    def read(content: str) -> M:
        try:
            return model.model_validate(parse_json(content))
        except ValidationError as exc:
            raise AnswerShapeError(f"{exc.error_count()} values break the schema") from exc

    return read


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema(by_alias=True)


def values_schema(keys: Sequence[str]) -> dict[str, Any]:
    """A generic field read: one string-or-null per requested key."""
    value = {"type": ["string", "null"], "maxLength": _VALUE_LENGTH}
    return {
        "type": "object",
        "properties": dict.fromkeys(keys, value),
        "required": list(keys),
        "additionalProperties": False,
    }


def values_reader(keys: Sequence[str]) -> Callable[[str], dict[str, str | None]]:
    def read(content: str) -> dict[str, str | None]:
        answer = parse_json(content)
        if not isinstance(answer, dict) or not set(keys) <= set(answer):
            raise AnswerShapeError(f"expected an object with the keys {', '.join(keys)}")
        values: dict[str, str | None] = {}
        for key in keys:
            raw = answer[key]
            if raw is not None and not isinstance(raw, str):
                raise AnswerShapeError(f"{key} is not a string or null")
            values[key] = " ".join(raw.split())[:_VALUE_LENGTH] or None if raw else None
        return values

    return read


# --- page-read: a whole page as blocks (Sumukh's model-first reading, services/conversions/prompts/read_page) -----

_MAX_BLOCKS = 200
_MAX_TABLE_COLUMNS = 50
_MAX_TABLE_ROWS = 1000


class PageAnalysis(_Answer):
    """Written before the blocks, so the model commits to a block count and table widths first."""

    n_blocks: int = Field(ge=0, le=_MAX_BLOCKS)
    table_column_counts: list[int] = Field(default_factory=list, max_length=10)


class PageTextBlock(_Answer):
    kind: Literal["heading", "paragraph", "caption", "list", "image"]
    text: str = Field(max_length=20_000)
    align: Literal["left", "center", "right"] = "left"
    size: Literal["large", "normal", "small"] = "normal"
    bold: bool = False


class PageTableBlock(_Answer):
    kind: Literal["table"]
    has_header: bool
    n_data_rows: int = Field(ge=0, le=_MAX_TABLE_ROWS)
    n_cols: int = Field(ge=1, le=_MAX_TABLE_COLUMNS)
    header: list[str] = Field(default_factory=list, max_length=_MAX_TABLE_COLUMNS)
    rows: list[list[str]] = Field(default_factory=list, max_length=_MAX_TABLE_ROWS)


class PageReadAnswer(_Answer):
    analysis: PageAnalysis  # first: the field order is the order the model writes them (see the prompt)
    blocks: list[Annotated[PageTextBlock | PageTableBlock, Field(discriminator="kind")]] = Field(max_length=_MAX_BLOCKS)

    @model_validator(mode="before")
    @classmethod
    def _kind_when_missing(cls, data: Any) -> Any:
        """A block without its ``kind`` is a table when it has rows or a header, else a paragraph. A host that
        enforces the schema always writes it; without enforcement (mlx_vlm.server) a small model leaves it out."""
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
            return data
        blocks = [
            {**block, "kind": "table" if "rows" in block or "header" in block else "paragraph"}
            if isinstance(block, dict) and "kind" not in block
            else block
            for block in data["blocks"]
        ]
        return {**data, "blocks": blocks}


# --- generic-read: any document, every field it shows (extraction's synchronous path, ADR 0003) ------------------

_DOCUMENT_TYPE_LENGTH = 64
_UNSAFE_TYPE_CHARACTERS = re.compile(r"[^a-z0-9]+")


class GenericTable(_Answer):
    headers: list[str] = Field(max_length=_MAX_TABLE_COLUMNS)
    rows: list[list[str]] = Field(max_length=_MAX_TABLE_ROWS)


class GenericField(_Answer):
    key: str = Field(pattern=_KEY)
    label: str = Field(max_length=120)
    type: Literal["string", "number", "date", "table"]
    value: str | GenericTable | None
    confidence: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def _value_fits_the_type(self) -> Self:
        is_table = isinstance(self.value, GenericTable)
        if self.value is not None and is_table != (self.type == "table"):
            raise ValueError("a table field needs headers and rows, any other field a text value")
        if isinstance(self.value, str) and len(self.value) > _VALUE_LENGTH * 5:
            raise ValueError("a value is too long")
        return self


class GenericReadAnswer(_Answer):
    """The model writes the type first, then the fields; ``tidied`` turns that into what callers get."""

    document_type: str | None = Field(max_length=_DOCUMENT_TYPE_LENGTH)
    document_confidence: float | None = Field(None, ge=0, le=1)
    fields: list[GenericField] = Field(max_length=_MAX_FIELDS)

    def tidied(self) -> "GenericReadAnswer":
        """The type as a lower snake_case code (``"Aadhaar Card"`` -> ``"aadhaar_card"``), no confidence without a
        value, empty text as null, and only the first field with each key (front and back read as one document)."""
        code = _UNSAFE_TYPE_CHARACTERS.sub("_", (self.document_type or "").lower()).strip("_")
        seen: set[str] = set()
        fields: list[GenericField] = []
        for found in self.fields:
            if found.key in seen:
                continue
            seen.add(found.key)
            value = " ".join(found.value.split()) or None if isinstance(found.value, str) else found.value
            confidence = found.confidence if value is not None else None
            fields.append(found.model_copy(update={"value": value, "confidence": confidence}))
        return self.model_copy(
            update={
                "document_type": code or None,
                "document_confidence": self.document_confidence if code else None,
                "fields": fields,
            }
        )
