"""ORM rows <-> domain objects. The JSON columns are checked by Pydantic on the way out of the database (DB-09)."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from extraction.core.error_codes import ErrorCode
from extraction.pipeline.domain.jobs import (
    ExtractionCompleted,
    ExtractionItem,
    ExtractionRequest,
    ItemContext,
    ItemStatus,
    ObjectRef,
    ResultSummary,
)
from extraction.pipeline.infrastructure.persistence.orm import ExtractionItemOrm


class _Stored(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _StoredRef(_Stored):
    bucket: str
    key: str


class StoredRequest(_Stored):
    files: list[_StoredRef]
    template_id: str | None
    options: dict[str, Any]


class StoredCompletion(_Stored):
    result: _StoredRef
    document_type: str | None
    template_id: str | None
    template_version: int | None
    fields: int
    needs_review: int
    avg_confidence: float
    usage_pages: int
    usage_ai_calls: int
    sequence: int


def to_orm(item: ExtractionItem) -> ExtractionItemOrm:
    context = item.request.context
    row = ExtractionItemOrm(
        item_id=context.item_id,
        tenant_id=context.tenant_id,
        job_id=context.job_id,
        document_id=item.request.document_id,
        correlation_id=context.correlation_id,
        created_at=item.created_at,
    )
    update_orm(row, item)
    return row


def update_orm(row: ExtractionItemOrm, item: ExtractionItem) -> None:
    request = item.request
    row.status = item.status
    row.attempts = item.attempts
    row.sequence = item.sequence
    row.correlation_id = request.context.correlation_id
    row.request = StoredRequest(
        files=[_StoredRef(bucket=f.bucket, key=f.key) for f in request.files],
        template_id=request.template_id,
        options=dict(request.options),
    ).model_dump(mode="json")
    row.completion = _completion_json(item.completion) if item.completion else None
    row.error_code = item.error_code
    row.updated_at = item.updated_at


def to_domain(row: ExtractionItemOrm) -> ExtractionItem:
    context = ItemContext(
        tenant_id=row.tenant_id, correlation_id=row.correlation_id, job_id=row.job_id, item_id=row.item_id
    )
    stored = StoredRequest.model_validate(row.request)
    request = ExtractionRequest(
        context=context,
        document_id=row.document_id,
        files=tuple(ObjectRef(bucket=f.bucket, key=f.key) for f in stored.files),
        template_id=stored.template_id,
        options=stored.options,
    )
    return ExtractionItem(
        request=request,
        status=ItemStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        attempts=row.attempts,
        sequence=row.sequence,
        completion=_completion(row.completion, context) if row.completion else None,
        error_code=ErrorCode(row.error_code) if row.error_code else None,
    )


def _completion_json(completion: ExtractionCompleted) -> dict[str, Any]:
    return StoredCompletion(
        result=_StoredRef(bucket=completion.result.bucket, key=completion.result.key),
        document_type=completion.document_type,
        template_id=completion.template_id,
        template_version=completion.template_version,
        fields=completion.summary.fields,
        needs_review=completion.summary.needs_review,
        avg_confidence=completion.summary.avg_confidence,
        usage_pages=completion.usage_pages,
        usage_ai_calls=completion.usage_ai_calls,
        sequence=completion.sequence,
    ).model_dump(mode="json")


def _completion(data: dict[str, Any], context: ItemContext) -> ExtractionCompleted:
    stored = StoredCompletion.model_validate(data)
    return ExtractionCompleted(
        item=context,
        result=ObjectRef(bucket=stored.result.bucket, key=stored.result.key),
        document_type=stored.document_type,
        template_id=stored.template_id,
        template_version=stored.template_version,
        summary=ResultSummary(
            fields=stored.fields, needs_review=stored.needs_review, avg_confidence=stored.avg_confidence
        ),
        usage_pages=stored.usage_pages,
        usage_ai_calls=stored.usage_ai_calls,
        sequence=stored.sequence,
    )
