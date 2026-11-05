"""From the application's result to the response body; the body never goes further than this (OBJ-01)."""

from pydantic import TypeAdapter, ValidationError

from extraction.pipeline.domain.custom_template import CustomField, CustomType
from extraction.quick.api.schemas import CustomFieldBody, ExtractedFieldBody, QuickExtractResponse, TableBody
from extraction.quick.domain.exceptions import FieldsInvalidError
from extraction.quick.domain.models import QuickResult, Table


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
    """The ``fields`` part: a JSON list of ``{key, label, type?}``. Raises ``FieldsInvalidError`` when it isn't one."""
    try:
        bodies = _FIELDS.validate_json(raw)
    except ValidationError as exc:
        message = "fields must be a JSON list of {key, label, type}: key lower snake_case, type text, date or number."
        raise FieldsInvalidError(message) from exc
    return [CustomField(key=b.key, label=b.label, type=CustomType(b.type)) for b in bodies]
