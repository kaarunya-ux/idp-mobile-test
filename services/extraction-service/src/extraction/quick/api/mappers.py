"""From the application's result to the response body; the body never goes further than this (OBJ-01)."""

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from extraction.pipeline.domain.custom_template import CustomField, CustomType
from extraction.quick.api.schemas import (
    CustomBoxBody,
    CustomFieldBody,
    ExtractedFieldBody,
    FieldBoxBody,
    QuickExtractResponse,
    TableBody,
)
from extraction.quick.domain.exceptions import FieldsInvalidError
from extraction.quick.domain.models import QuickResult, Table
from extraction.quick.domain.placed import PlacedBox
from idp_ocr_engines.model import Box


def to_response(result: QuickResult, ids: Mapping[str, str] | None = None) -> QuickExtractResponse:
    """``ids``: key -> the id the caller sent with it (the custom route), echoed on that key's field."""
    ids = ids or {}
    return QuickExtractResponse(
        document_type=result.document_type,
        document_confidence=result.document_confidence,
        fields=[
            ExtractedFieldBody(
                id=ids.get(f.key),
                key=f.key,
                label=f.label,
                type=f.type,
                value=TableBody(headers=list(f.value.headers), rows=[list(r) for r in f.value.rows])
                if isinstance(f.value, Table)
                else f.value,
                confidence=f.confidence,
                status=f.status,
                source=f.source,
                box=_box_body(f.box),
            )
            for f in result.fields
        ],
        read_by=result.read_by.value,
        model=result.model_id,
        prompt_version=result.prompt_version,
    )


_FIELDS = TypeAdapter(list[CustomFieldBody])


def custom_fields(raw: str) -> tuple[list[CustomField], dict[str, str]]:
    """The ``fields`` part, a JSON list of ``{id?, key, label, type?, box?}``: the keys, and key -> id for the keys
    sent with one. Raises ``FieldsInvalidError`` when it isn't such a list or an id is used twice."""
    try:
        bodies = _FIELDS.validate_json(raw)
    except ValidationError as exc:
        message = (
            "fields must be a JSON list of {id, key, label, type, box}: id optional, key lower snake_case, type text, "
            "date or number, box {page: 1, x, y, w, h} in percent of the page."
        )
        raise FieldsInvalidError(message) from exc
    ids = {b.key: b.id for b in bodies if b.id is not None}
    if len(set(ids.values())) != len(ids):
        raise FieldsInvalidError("Each id may be used only once.")
    keys = [CustomField(key=b.key, label=b.label, type=CustomType(b.type), box=_box(b.box)) for b in bodies]
    return keys, ids


def _box(body: CustomBoxBody | None) -> Box | None:
    """Percent of the page to the fractions OCR boxes use, cut at the page's edge."""
    if body is None:
        return None
    x, y = body.x / 100, body.y / 100
    return Box(x=x, y=y, width=min(body.w / 100, 1 - x), height=min(body.h / 100, 1 - y))


_PERCENT = 100
_DECIMALS = 2


def _box_body(placed: PlacedBox | None) -> FieldBoxBody | None:
    """Fractions of the page to the percent form a custom key's box uses."""
    if placed is None:
        return None
    box = placed.box
    return FieldBoxBody(
        page=placed.page,
        x=round(box.x * _PERCENT, _DECIMALS),
        y=round(box.y * _PERCENT, _DECIMALS),
        w=round(box.width * _PERCENT, _DECIMALS),
        h=round(box.height * _PERCENT, _DECIMALS),
    )
