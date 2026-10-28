"""The smallest answer that fits a JSON schema, for questions with no recording.

Nullable values are null, enums take their first non-null value, numbers their minimum (or 0.5 between 0 and 1),
strings ``"mock"``, arrays their ``prefixItems`` or as few items as allowed, objects their required properties.
``$ref`` into ``$defs`` is followed, as Pydantic writes them.
"""

from collections.abc import Callable, Mapping
from typing import Any

_MOCK_TEXT = "mock"
_MAX_DEPTH = 20


def answer_for_schema(schema: Mapping[str, Any]) -> Any:
    return _value(schema, schema.get("$defs", {}), 0)


def _value(schema: Mapping[str, Any], defs: Mapping[str, Any], depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return None
    if "$ref" in schema:
        return _value(defs.get(str(schema["$ref"]).rsplit("/", 1)[-1], {}), defs, depth + 1)
    if "enum" in schema:
        return next((v for v in schema["enum"] if v is not None), None)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        nullable = any(o.get("type") == "null" for o in options)
        return None if nullable else _value(options[0], defs, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0]
    build = _BUILDERS.get(str(kind))
    return build(schema, defs, depth) if build else None


def _object(schema: Mapping[str, Any], defs: Mapping[str, Any], depth: int) -> dict[str, Any]:
    properties = schema.get("properties", {})
    return {name: _value(properties.get(name, {}), defs, depth + 1) for name in schema.get("required", [])}


def _array(schema: Mapping[str, Any], defs: Mapping[str, Any], depth: int) -> list[Any]:
    if "prefixItems" in schema:
        return [_value(item, defs, depth + 1) for item in schema["prefixItems"]]
    return [_value(schema.get("items", {}), defs, depth + 1) for _ in range(int(schema.get("minItems", 0)))]


_BUILDERS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any], int], Any]] = {
    "object": _object,
    "array": _array,
    "number": lambda schema, _defs, _depth: _number(schema, integer=False),
    "integer": lambda schema, _defs, _depth: _number(schema, integer=True),
    "boolean": lambda _schema, _defs, _depth: False,
    "string": lambda _schema, _defs, _depth: _MOCK_TEXT,
}


def _number(schema: Mapping[str, Any], *, integer: bool) -> float | int:
    low, high = schema.get("minimum"), schema.get("maximum")
    if low == 0 and high == 1 and not integer:
        return 0.5
    value = low if low is not None else 0
    return int(value) if integer else float(value)
