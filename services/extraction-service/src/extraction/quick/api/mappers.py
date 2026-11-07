"""From the application's result to the response body; the body never goes further than this (OBJ-01)."""

from pydantic import TypeAdapter, ValidationError

from extraction.pipeline.domain.custom_template import CustomField, CustomType
from extraction.quick.api.schemas import (
    CustomBoxBody,
    CustomFieldBody,
    ExtractedFieldBody,
    QuickExtractResponse,
    TableBody,
)
from extraction.quick.domain.exceptions import FieldsInvalidError
from extraction.quick.domain.models import QuickResult, Table
from idp_ocr_engines.model import Box


def to_response(result: QuickResult) -> QuickExtractResponse:
    return QuickExtractResponse(
        document_type=result.document_type,
        document_confidence=result.document_confidence,
        fields=[
            ExtractedFieldBody(
                key=f.key,
                label=f.label,
                type=f.type,
                value=TableBody(headers=list(f.value.headers), rows=[list(r) for r in f.value.rows])
                if isinstance(f.value, Table)
                else f.value,
                confidence=f.confidence,
                status=f.status,
                source=f.source,
            )
            for f in result.fields
        ],
        read_by=result.read_by.value,
        model=result.model_id,
        prompt_version=result.prompt_version,
    )


_FIELDS = TypeAdapter(list[CustomFieldBody])


def custom_fields(raw: str) -> list[CustomField]:
    """The ``fields`` part: a JSON list of ``{key, label, type?, box?}``. Raises ``FieldsInvalidError`` when it isn't
    one."""
    try:
        bodies = _FIELDS.validate_json(raw)
    except ValidationError as exc:
        message = (
            "fields must be a JSON list of {key, label, type, box}: key lower snake_case, type text, date or number, "
            "box {page: 1, x, y, w, h} in percent of the page."
        )
        raise FieldsInvalidError(message) from exc
    return [CustomField(key=b.key, label=b.label, type=CustomType(b.type), box=_box(b.box)) for b in bodies]


def _box(body: CustomBoxBody | None) -> Box | None:
    """Percent of the page to the fractions OCR boxes use, cut at the page's edge."""
    if body is None:
        return None
    x, y = body.x / 100, body.y / 100
    return Box(x=x, y=y, width=min(body.w / 100, 1 - x), height=min(body.h / 100, 1 - y))
