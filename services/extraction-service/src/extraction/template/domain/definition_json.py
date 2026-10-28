"""Template definition JSON, schema version 1 (IDP-03 ch. 2.5) <-> the pipeline's ``TemplateDefinition``.

The JSON is what the API accepts and ``template_versions.definition`` stores; the pipeline reads the parsed form.
``to_json(parse(x))`` keeps every value, and the standard templates survive the round trip unchanged (tested).

Beyond the schema, a field may carry the reading hints the pipeline uses (``labels``, ``extract``, ``above``,
``maxLinesAbove``, ``excludes``, ``keep``, ``startsAt``, ``language``; extraction-service ADR 0003). Parts of the
schema the pipeline can't use yet are refused, not ignored: field types other than TEXT, NAME, ID, DATE, NUMBER and
ADDRESS; rule kinds other than REGEX, DATE and CUSTOM; regions on pages other than 0; and ``tables``.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Any

from extraction.pipeline.domain.template import (
    Accept,
    AcceptMode,
    Anchor,
    FieldDefinition,
    FieldType,
    Mask,
    RuleKind,
    RuleSpec,
    TemplateDefinition,
)
from extraction.rule.normalizers import Normalizer
from extraction.template.domain.exceptions import DefinitionInvalidError
from idp_ocr_engines.model import Box

SCHEMA_VERSION = 1
_TODAY = "TODAY"
_DEFAULT_EARLIEST = date(1900, 1, 1)
_VALIDATORS = frozenset({"PAN", "AADHAAR"})
_BOX_LENGTH = 4  # x, y, width, height
_HINTS = {  # JSON key -> FieldDefinition attribute, for the optional reading hints
    "labels": "labels",
    "extract": "extract",
    "above": "above",
    "keep": "keep",
    "startsAt": "starts_at",
}


class _Problems:
    """Collects every problem, so one response lists them all (not just the first)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append((path, message))

    def raise_if_any(self) -> None:
        if self.items:
            raise DefinitionInvalidError(self.items)


def parse_definition(data: object, *, template_id: str, version: int, scope: str) -> TemplateDefinition:
    """Check and read a definition. Raise ``DefinitionInvalidError`` (EXT-422-DEFINITION-001) listing every problem."""
    problems = _Problems()
    root = _object(data, "", problems)
    if root.get("schemaVersion") != SCHEMA_VERSION:
        problems.add("schemaVersion", "must be 1")
    if root.get("tables"):
        problems.add("tables", "tables are not supported yet")
    page = _object(root.get("page", {}), "page", problems)
    classifier = _object(root.get("classifier", {}), "classifier", problems)
    anchors = [
        _anchor(a, f"anchors[{n}]", problems) for n, a in enumerate(_list(root.get("anchors", []), "anchors", problems))
    ]
    fields = [_field(f, f"fields[{n}]", problems) for n, f in enumerate(_list(root.get("fields"), "fields", problems))]
    keys = [f.key for f in fields if f is not None]
    if not keys:
        problems.add("fields", "at least one field is needed")
    if len(set(keys)) != len(keys):
        problems.add("fields", "field keys must be unique")
    anchor_ids = {a.id for a in anchors if a is not None}
    for n, f in enumerate(fields):
        if f is not None and f.relative_to is not None and f.relative_to not in anchor_ids:
            problems.add(f"fields[{n}].region.relativeTo", f"no anchor named {f.relative_to}")
    document_type = _text(root, "documentType", "", problems)
    keywords = tuple(_strings(classifier.get("keywords", []), "classifier.keywords", problems))
    for n, keyword in enumerate(keywords):
        _check_regex(keyword, f"classifier.keywords[{n}]", problems)
    problems.raise_if_any()
    return TemplateDefinition(
        template_id=template_id,
        version=version,
        scope=scope,
        document_type=document_type,
        aspect_ratio=_number(page, "aspectRatio", 1.586, "page", problems),
        aspect_tolerance=_number(page, "aspectTolerance", 0.25, "page", problems),
        keywords=keywords,
        min_keyword_hits=int(_number(classifier, "minKeywordHits", 1, "classifier", problems)),
        anchors=tuple(a for a in anchors if a is not None),
        fields=tuple(f for f in fields if f is not None),
    )


def check_definition(data: object, *, document_type: str) -> dict[str, Any]:
    """Validate a definition for storage; return it unchanged. The document type must be the template's."""
    definition = parse_definition(data, template_id="-", version=0, scope="-")
    if definition.document_type != document_type:
        raise DefinitionInvalidError([("documentType", f"must be the template's document type, {document_type}")])
    return dict(data) if isinstance(data, Mapping) else {}


def to_json(definition: TemplateDefinition) -> dict[str, Any]:
    """The schema v1 JSON of a pipeline definition (used to store the standard templates)."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "documentType": definition.document_type,
        "page": {"count": 1, "aspectRatio": definition.aspect_ratio, "aspectTolerance": definition.aspect_tolerance},
        "anchors": [
            {
                "id": a.id,
                "text": a.text,
                "match": "FUZZY",
                "minSimilarity": a.min_similarity,
                "region": _box_json(a.region),
            }
            for a in definition.anchors
        ],
        "fields": [_field_json(f) for f in definition.fields],
        "tables": [],
        "classifier": {"keywords": list(definition.keywords), "minKeywordHits": definition.min_keyword_hits},
    }


# --- Fields ---------------------------------------------------------------------------------------------------


def _field(data: object, path: str, problems: _Problems) -> FieldDefinition | None:
    raw = _object(data, path, problems)
    region = _object(raw.get("region"), f"{path}.region", problems)
    if region.get("page", 0) != 0:
        problems.add(f"{path}.region.page", "only page 0 is read for now")
    field_type = _enum(FieldType, raw.get("type"), f"{path}.type", problems)
    rule_data = _object(raw.get("rule"), f"{path}.rule", problems)
    rule = _rule(rule_data, f"{path}.rule", problems)
    normalize = tuple(
        n
        for n in (
            _enum(Normalizer, v, f"{path}.rule.normalize", problems)
            for v in _list(rule_data.get("normalize", []), f"{path}.rule.normalize", problems)
        )
        if n
    )
    box = _box(region.get("box"), f"{path}.region.box", problems)
    hints = {attr: _optional_regex(raw, key, f"{path}.{key}", problems) for key, attr in _HINTS.items()}
    excludes = tuple(_strings(raw.get("excludes", []), f"{path}.excludes", problems))
    start = len(problems.items)
    key, label = _text(raw, "key", path, problems), _text(raw, "label", path, problems)
    if field_type is None or rule is None or box is None or len(problems.items) > start:
        return None
    try:
        return FieldDefinition(
            key=key,
            label=label,
            type=field_type,
            box=box,
            rule=rule,
            normalize=normalize,
            required=bool(raw.get("required", True)),
            relative_to=region.get("relativeTo"),
            max_lines_above=int(raw.get("maxLinesAbove", 3)),
            excludes=excludes,
            language=str(raw.get("language", "en")),
            accept=_accept(_object(raw.get("accept", {}), f"{path}.accept", problems), f"{path}.accept", problems),
            mask=_mask(raw.get("mask"), f"{path}.mask", problems),
            **hints,
        )
    except (ValueError, TypeError) as exc:
        problems.add(path, str(exc))
        return None


def _rule(data: Mapping[str, Any], path: str, problems: _Problems) -> RuleSpec | None:
    kind = _enum(
        RuleKind, data.get("kind"), f"{path}.kind", problems, unsupported="only REGEX, DATE and CUSTOM for now"
    )
    rule_id = _text(data, "id", path, problems)
    pattern = data.get("pattern")
    if pattern is not None:
        _check_regex(pattern, f"{path}.pattern", problems)
    if kind is RuleKind.REGEX and not pattern:
        problems.add(f"{path}.pattern", "a REGEX rule needs a pattern")
    validator = data.get("validator")
    if kind is RuleKind.CUSTOM and validator not in _VALIDATORS:
        problems.add(f"{path}.validator", f"one of {', '.join(sorted(_VALIDATORS))}")
    earliest = _date(data.get("min"), f"{path}.min", problems) or _DEFAULT_EARLIEST
    latest = None if data.get("max") in (None, _TODAY) else _date(data.get("max"), f"{path}.max", problems)
    if kind is None:
        return None
    return RuleSpec(
        id=rule_id,
        kind=kind,
        pattern=pattern,
        formats=tuple(_strings(data.get("formats", []), f"{path}.formats", problems)),
        earliest=earliest,
        latest=latest,
        validator=validator,
    )


def _accept(data: Mapping[str, Any], path: str, problems: _Problems) -> Accept:
    mode = _enum(AcceptMode, data.get("mode", "EXACT"), f"{path}.mode", problems) or AcceptMode.EXACT
    threshold = data.get("threshold")
    if threshold is not None and not (isinstance(threshold, int | float) and 0 < threshold <= 1):
        problems.add(f"{path}.threshold", "a number above 0, up to 1")
        threshold = None
    return Accept(mode=mode, threshold=float(threshold) if threshold is not None else None)


def _mask(data: object, path: str, problems: _Problems) -> Mask | None:
    if data is None:
        return None
    raw = _object(data, path, problems)
    keep = raw.get("keepLast")
    if not isinstance(keep, int) or keep < 0:
        problems.add(f"{path}.keepLast", "a whole number, 0 or more")
        return None
    return Mask(keep_last=keep, char=str(raw.get("char", "X"))[:1] or "X")


def _anchor(data: object, path: str, problems: _Problems) -> Anchor | None:
    raw = _object(data, path, problems)
    box = _box(raw.get("region"), f"{path}.region", problems)
    if box is None:
        return None
    return Anchor(
        id=_text(raw, "id", path, problems),
        text=_text(raw, "text", path, problems),
        region=box,
        min_similarity=_number(raw, "minSimilarity", 0.8, path, problems),
    )


def _field_json(f: FieldDefinition) -> dict[str, Any]:
    rule: dict[str, Any] = {"id": f.rule.id, "kind": f.rule.kind.value, "normalize": [n.value for n in f.normalize]}
    if f.rule.pattern is not None:
        rule["pattern"] = f.rule.pattern
    if f.rule.formats:
        rule["formats"] = list(f.rule.formats)
    if f.rule.validator is not None:
        rule["validator"] = f.rule.validator
    if f.rule.earliest != _DEFAULT_EARLIEST:
        rule["min"] = f.rule.earliest.isoformat()
    rule["max"] = f.rule.latest.isoformat() if f.rule.latest else _TODAY
    region: dict[str, Any] = {"page": 0, "box": _box_json(f.box)}
    if f.relative_to is not None:
        region["relativeTo"] = f.relative_to
    out: dict[str, Any] = {
        "key": f.key,
        "label": f.label,
        "type": f.type.value,
        "required": f.required,
        "region": region,
        "rule": rule,
        "accept": {"mode": f.accept.mode.value} | ({"threshold": f.accept.threshold} if f.accept.threshold else {}),
        "mask": {"keepLast": f.mask.keep_last, "char": f.mask.char} if f.mask else None,
        "language": f.language,
        "maxLinesAbove": f.max_lines_above,
    }
    for key, attr in _HINTS.items():
        if getattr(f, attr) is not None:
            out[key] = getattr(f, attr)
    if f.excludes:
        out["excludes"] = list(f.excludes)
    return out


# --- Small readers --------------------------------------------------------------------------------------------


def _object(value: object, path: str, problems: _Problems) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    problems.add(path or "(definition)", "must be an object")
    return {}


def _list(value: object, path: str, problems: _Problems) -> Sequence[Any]:
    if isinstance(value, list):
        return value
    problems.add(path, "must be a list")
    return []


def _strings(value: object, path: str, problems: _Problems) -> list[str]:
    items = _list(value, path, problems)
    if not all(isinstance(item, str) for item in items):
        problems.add(path, "must be a list of text")
        return []
    return list(items)


def _text(data: Mapping[str, Any], key: str, path: str, problems: _Problems) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        problems.add(f"{path}.{key}" if path else key, "must be text")
        return ""
    return value


def _number(data: Mapping[str, Any], key: str, default: float, path: str, problems: _Problems) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        problems.add(f"{path}.{key}", "must be a number, 0 or more")
        return default
    return float(value)


def _box(value: object, path: str, problems: _Problems) -> Box | None:
    if not (isinstance(value, list) and len(value) == _BOX_LENGTH and all(isinstance(v, int | float) for v in value)):
        problems.add(path, "[x, y, width, height] as page fractions")
        return None
    x, y, width, height = (float(v) for v in value)
    try:
        return Box(x=x, y=y, width=width, height=height)
    except ValueError as exc:
        problems.add(path, str(exc))
        return None


def _box_json(box: Box) -> list[float]:
    return [box.x, box.y, box.width, box.height]


def _enum[E: StrEnum](
    kind: type[E], value: object, path: str, problems: _Problems, *, unsupported: str = ""
) -> E | None:
    try:
        return kind(str(value))
    except ValueError:
        names = ", ".join(str(m) for m in kind)
        problems.add(path, unsupported or f"one of {names}")
        return None


def _date(value: object, path: str, problems: _Problems) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        problems.add(path, "a date as YYYY-MM-DD, or TODAY")
        return None


def _optional_regex(data: Mapping[str, Any], key: str, path: str, problems: _Problems) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        problems.add(path, "must be text")
        return None
    _check_regex(value, path, problems)
    return value


def _check_regex(value: object, path: str, problems: _Problems) -> None:
    try:
        re.compile(str(value))
    except re.error as exc:
        problems.add(path, f"not a valid regular expression: {exc.msg}")
