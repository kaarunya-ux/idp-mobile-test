"""Reading a model's JSON answer (LLM-03): tolerant of a fenced or chatty answer, strict about its shape.

Hosts that don't enforce ``response_format`` wrap JSON in a code fence or add a sentence; the JSON is still found.
Anything that isn't the requested shape raises ``AnswerShapeError``, and the runner asks once more.
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ai_gateway.ai.domain.card_profiles import CardProfile
from ai_gateway.ai.domain.exceptions import AnswerShapeError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(content: str) -> object:
    """The JSON in an answer, also when it is fenced or follows a sentence."""
    text = content.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
        if not starts:
            raise AnswerShapeError("no JSON in the answer") from None
        try:
            return json.JSONDecoder().raw_decode(text[min(starts) :])[0]
        except json.JSONDecodeError as exc:
            raise AnswerShapeError("the JSON in the answer is not complete") from exc


def card_values(content: str, profile: CardProfile) -> dict[str, str | None]:
    """A card answer as ``{template field key: value}``. A value that breaks its schema becomes None, not an error:
    the caller's rules decide what to do with a missing value, and one bad field shouldn't lose the others."""
    values = _as_list(parse_json(content), list(profile.fields))
    result: dict[str, str | None] = {}
    for (name, schema), raw in zip(profile.fields.items(), values, strict=True):
        key = profile.field_keys.get(name)
        if key is None:
            continue
        value = " ".join(raw.split()) if isinstance(raw, str) else None
        result[key] = profile.values.get(name, {}).get(value, value) if value and conforms(schema, value) else None
    return result


def _as_list(values: object, names: Sequence[str]) -> list[object]:
    """The values in field order; an object keyed by every field name is accepted too."""
    if isinstance(values, dict) and all(name in values for name in names):
        values = [values[name] for name in names]
    if not isinstance(values, list) or len(values) != len(names):
        raise AnswerShapeError(f"expected a JSON array of {len(names)} values")
    return values


def conforms(schema: Mapping[str, Any], value: str) -> bool:
    """Whether a value meets its schema (enum, pattern, maxLength)."""
    if "enum" in schema:
        return value in schema["enum"]
    for option in schema.get("anyOf", [schema]):
        if option.get("type") == "null":
            continue
        if "pattern" in option and not re.fullmatch(option["pattern"].strip("^$"), value):
            continue
        if len(value) > option.get("maxLength", len(value)):
            continue
        return True
    return False
