"""The shape each task's answer must have (LLM-03): a Pydantic model to validate it, and the JSON schema sent as
``response_format`` so a host that enforces schemas can only answer in this shape."""

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
