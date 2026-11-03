"""From the application's result to the response body; the body never goes further than this (OBJ-01)."""

from extraction.quick.api.schemas import ExtractedFieldBody, QuickExtractResponse, TableBody
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
