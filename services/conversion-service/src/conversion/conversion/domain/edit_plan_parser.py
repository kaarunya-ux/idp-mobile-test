"""Reads the PDF edit plan JSON into ``edit_plan`` values, checking every field (IDP-03 ch. 3.5; MSG-06)."""

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from conversion.conversion.domain.edit_plan import (
    MAX_OPERATIONS,
    MAX_OUTPUT_PAGES,
    AnnotateText,
    BlankPage,
    Box,
    Color,
    Compress,
    CompressPreset,
    EditPlan,
    FillForm,
    Highlight,
    Ink,
    Metadata,
    Operation,
    PageNumbers,
    PageSize,
    PageSpec,
    PlaceImage,
    Position,
    Protect,
    RedactArea,
    RedactText,
    ReplaceText,
    SourcePage,
    Split,
    SplitMode,
    Watermark,
)
from conversion.conversion.domain.exceptions import InvalidOptionError, InvalidPlanError, LimitExceededError
from conversion.conversion.domain.options import PageRange

_ROTATIONS = frozenset({0, 90, 180, 270})
_EDGE = 1.000001  # a box may touch the page edge; this absorbs rounding in the editor's fractions


def parse_edit_plan(raw: object) -> EditPlan:
    """Read and check the plan. Raise ``InvalidPlanError`` (or ``LimitExceededError`` for its size)."""
    plan = _mapping(raw, "plan")
    if plan.get("schemaVersion") != 1:
        raise InvalidPlanError("Only edit plan schema version 1 is supported.")
    pages = None if plan.get("pages") is None else tuple(_page(p) for p in _list(plan["pages"], "pages"))
    if pages is not None and not 1 <= len(pages) <= MAX_OUTPUT_PAGES:
        raise LimitExceededError(f"An edit plan makes 1 to {MAX_OUTPUT_PAGES:,} pages.")
    operations = _list(plan.get("operations", []), "operations")
    if len(operations) > MAX_OPERATIONS:
        raise LimitExceededError(f"An edit plan has at most {MAX_OPERATIONS} operations.")
    output = _mapping(plan.get("output") or {}, "output")
    return EditPlan(
        pages=pages,
        operations=tuple(_operation(op) for op in operations),
        split=_split(output["split"]) if output.get("split") else None,
        password=_optional_text(plan, "password"),
    )


def _page(raw: object) -> PageSpec:
    page = _mapping(raw, "page")
    if "blank" in page:
        blank = _mapping(page["blank"], "blank")
        orientation = _text(blank, "orientation", "PORTRAIT")
        if orientation not in ("PORTRAIT", "LANDSCAPE"):
            raise InvalidPlanError("A blank page's orientation is PORTRAIT or LANDSCAPE.")
        return BlankPage(size=_enum(PageSize, _text(blank, "size", "A4"), "size"), landscape=orientation == "LANDSCAPE")
    source = _mapping(page.get("source"), "source")
    rotate = _int(page, "rotate", 0)
    if rotate not in _ROTATIONS:
        raise InvalidPlanError("A page's rotation is 0, 90, 180 or 270.")
    return SourcePage(document_id=_text(source, "documentId"), page=_positive(source, "page"), rotate=rotate)


def _operation(raw: object) -> Operation:
    op = _mapping(raw, "operation")
    reader = _OPERATION_READERS.get(str(op.get("type")))
    if reader is None:
        raise InvalidPlanError(f"Unknown operation type {op.get('type')!r}.")
    return reader(op)


def _annotate_text(op: Mapping[str, Any]) -> AnnotateText:
    return AnnotateText(
        page=_index(op),
        box=_box(op.get("box")),
        text=_text(op, "text"),
        font_size=_number(op, "fontSize", 12, low=4, high=144),
        color=_color(op, "#000000"),
    )


def _highlight(op: Mapping[str, Any]) -> Highlight:
    return Highlight(page=_index(op), boxes=_boxes(op), color=_color(op, "#FDE047"))


def _ink(op: Mapping[str, Any]) -> Ink:
    paths = tuple(tuple(_point(p) for p in _list(path, "path")) for path in _list(op.get("paths"), "paths"))
    return Ink(
        page=_index(op), paths=paths, width=_number(op, "width", 2, low=0.25, high=50), color=_color(op, "#000000")
    )


def _place_image(op: Mapping[str, Any]) -> PlaceImage:
    return PlaceImage(page=_index(op), box=_box(op.get("box")), image_key=_text(op, "imageKey"))


def _page_numbers(op: Mapping[str, Any]) -> PageNumbers:
    return PageNumbers(
        position=_enum(Position, _text(op, "position", "BOTTOM_CENTER"), "position"),
        format=_text(op, "format", "{n}"),
        start_at=_int(op, "startAt", 1),
    )


def _redact_area(op: Mapping[str, Any]) -> RedactArea:
    return RedactArea(page=_index(op), boxes=_boxes(op))


def _redact_text(op: Mapping[str, Any]) -> RedactText:
    return RedactText(query=_text(op, "query"), match_case=bool(op.get("matchCase", False)))


def _fill_form(op: Mapping[str, Any]) -> FillForm:
    fields = _mapping(op.get("fields"), "fields")
    return FillForm(fields={str(k): str(v) for k, v in fields.items()}, flatten=bool(op.get("flatten")))


def _replace_text(op: Mapping[str, Any]) -> ReplaceText:
    return ReplaceText(page=_index(op), find=_text(op, "find"), replace=str(op.get("replace", "")))


def _metadata(op: Mapping[str, Any]) -> Metadata:
    keys = ("title", "author", "subject", "keywords")
    return Metadata(changes={k: (None if op[k] is None else str(op[k])) for k in keys if k in op})


def _protect(op: Mapping[str, Any]) -> Protect:
    return Protect(
        user_password=_text(op, "userPassword", ""),
        owner_password=_text(op, "ownerPassword"),
        allow_print=bool(op.get("allowPrint", True)),
        allow_copy=bool(op.get("allowCopy", True)),
    )


def _compress(op: Mapping[str, Any]) -> Compress:
    return Compress(preset=_enum(CompressPreset, _text(op, "preset", "BALANCED"), "preset"))


def _watermark(op: Mapping[str, Any]) -> Watermark:
    pages = op.get("pages", "ALL")
    return Watermark(
        text=_text(op, "text"),
        opacity=_number(op, "opacity", 0.15, low=0.01, high=1),
        rotation=_number(op, "rotation", 45, low=-360, high=360),
        pages=None if pages == "ALL" else _page_range(pages),
    )


_OPERATION_READERS: Mapping[str, Callable[[Mapping[str, Any]], Operation]] = {
    "ANNOTATE_TEXT": _annotate_text,
    "HIGHLIGHT": _highlight,
    "INK": _ink,
    "SIGNATURE": _place_image,
    "INSERT_IMAGE": _place_image,
    "WATERMARK": _watermark,
    "PAGE_NUMBERS": _page_numbers,
    "REDACT_AREA": _redact_area,
    "REDACT_TEXT": _redact_text,
    "FILL_FORM": _fill_form,
    "REPLACE_TEXT": _replace_text,
    "METADATA": _metadata,
    "PROTECT": _protect,
    "COMPRESS": _compress,
}


def _split(raw: object) -> Split:
    split = _mapping(raw, "split")
    mode = _enum(SplitMode, _text(split, "mode"), "split mode")
    if mode is SplitMode.RANGES:
        ranges = tuple(_page_range(r) for r in _list(split.get("ranges"), "ranges"))
        if not ranges:
            raise InvalidPlanError("A RANGES split needs at least one range.")
        return Split(mode=mode, ranges=ranges)
    if mode is SplitMode.EVERY:
        return Split(mode=mode, every=_positive(split, "pages"))
    return Split(mode=mode)


# --- Small readers --------------------------------------------------------------------------------------------


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidPlanError(f"'{name}' must be an object.")
    return value


def _list(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise InvalidPlanError(f"'{name}' must be a list.")
    return value


def _text(data: Mapping[str, Any], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or (default is None and not value):
        raise InvalidPlanError(f"'{key}' must be text.")
    return value


def _optional_text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return None if value is None else _text(data, key)


def _int(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidPlanError(f"'{key}' must be a whole number.")
    return value


def _positive(data: Mapping[str, Any], key: str) -> int:
    value = _int(data, key, 0)
    if value < 1:
        raise InvalidPlanError(f"'{key}' must be 1 or more.")
    return value


def _index(op: Mapping[str, Any]) -> int:
    value = _int(op, "page", -1)
    if value < 0:
        raise InvalidPlanError(f"{op.get('type')} needs an output page index from 0.")
    return value


def _number(data: Mapping[str, Any], key: str, default: float, *, low: float, high: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise InvalidPlanError(f"'{key}' must be a number from {low} to {high}.")
    return float(value)


def _fraction(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise InvalidPlanError("Positions and sizes are fractions of the page, from 0 to 1.")
    return float(value)


def _box(value: object) -> Box:
    items = _list(value, "box")
    if len(items) != 4:  # noqa: PLR2004 - [x, y, width, height]
        raise InvalidPlanError("A box is [x, y, width, height].")
    x, y, width, height = (_fraction(v) for v in items)
    if width <= 0 or height <= 0 or x + width > _EDGE or y + height > _EDGE:
        raise InvalidPlanError("A box must have a size and fit on the page.")
    return (x, y, width, height)


def _boxes(op: Mapping[str, Any]) -> tuple[Box, ...]:
    boxes = tuple(_box(b) for b in _list(op.get("boxes"), "boxes"))
    if not boxes:
        raise InvalidPlanError(f"{op.get('type')} needs at least one box.")
    return boxes


def _point(value: object) -> tuple[float, float]:
    items = _list(value, "point")
    if len(items) != 2:  # noqa: PLR2004 - [x, y]
        raise InvalidPlanError("A point is [x, y].")
    return (_fraction(items[0]), _fraction(items[1]))


def _color(op: Mapping[str, Any], default: str) -> Color:
    value = _text(op, "color", default)
    if len(value) != 7 or not value.startswith("#"):  # noqa: PLR2004 - "#RRGGBB"
        raise InvalidPlanError("A colour is written #RRGGBB.")
    try:
        red, green, blue = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        raise InvalidPlanError("A colour is written #RRGGBB.") from None
    return (red, green, blue)


def _enum[E: StrEnum](kind: type[E], value: str, name: str) -> E:
    try:
        return kind(value)
    except ValueError:
        raise InvalidPlanError(f"Unknown {name} {value!r}.") from None


def _page_range(value: object) -> PageRange:
    if not isinstance(value, str):
        raise InvalidPlanError("A page range is text such as '1-3,5'.")
    try:
        return PageRange.parse(value)
    except InvalidOptionError as exc:
        raise InvalidPlanError(f"Page range {value!r} is not valid.") from exc
