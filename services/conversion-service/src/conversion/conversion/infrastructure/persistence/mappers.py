"""ORM rows <-> domain objects. The JSON columns are checked by Pydantic on the way out of the database (DB-09)."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from conversion.conversion.domain.events import ConversionCompleted, ItemContext
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, ItemStatus
from conversion.conversion.domain.values import InputFile, ObjectRef, StoredOutput
from conversion.conversion.infrastructure.persistence.orm import ConversionItemOrm
from conversion.core.error_codes import ErrorCode


class _Stored(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _StoredInput(_Stored):
    bucket: str
    key: str
    format: Format


class StoredRequest(_Stored):
    inputs: list[_StoredInput]
    target: Format
    options: dict[str, Any]
    output_prefix: str


class _StoredOutput(_Stored):
    bucket: str
    key: str
    content_type: str
    size: int
    sha256: str
    page_count: int | None


class StoredCompletion(_Stored):
    outputs: list[_StoredOutput]
    usage_pages: int
    sequence: int


def to_orm(item: ConversionItem) -> ConversionItemOrm:
    context = item.request.context
    row = ConversionItemOrm(
        item_id=context.item_id,
        tenant_id=context.tenant_id,
        job_id=context.job_id,
        correlation_id=context.correlation_id,
        created_at=item.created_at,
    )
    update_orm(row, item)
    return row


def update_orm(row: ConversionItemOrm, item: ConversionItem) -> None:
    request = item.request
    row.status = item.status
    row.attempts = item.attempts
    row.sequence = item.sequence
    row.correlation_id = request.context.correlation_id
    row.request = StoredRequest(
        inputs=[_StoredInput(bucket=i.ref.bucket, key=i.ref.key, format=i.format) for i in request.inputs],
        target=request.target,
        options=dict(request.options),
        output_prefix=request.output_prefix,
    ).model_dump(mode="json")
    row.completion = _completion_json(item.completion) if item.completion else None
    row.error_code = item.error_code
    row.updated_at = item.updated_at


def to_domain(row: ConversionItemOrm) -> ConversionItem:
    context = ItemContext(
        tenant_id=row.tenant_id, correlation_id=row.correlation_id, job_id=row.job_id, item_id=row.item_id
    )
    stored = StoredRequest.model_validate(row.request)
    request = ConversionRequest(
        context=context,
        inputs=tuple(InputFile(ref=ObjectRef(bucket=i.bucket, key=i.key), format=i.format) for i in stored.inputs),
        target=stored.target,
        options=stored.options,
        output_prefix=stored.output_prefix,
    )
    return ConversionItem(
        request=request,
        status=ItemStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        attempts=row.attempts,
        sequence=row.sequence,
        completion=_completion(row.completion, context) if row.completion else None,
        error_code=ErrorCode(row.error_code) if row.error_code else None,
    )


def _completion_json(completion: ConversionCompleted) -> dict[str, Any]:
    outputs = [
        _StoredOutput(
            bucket=o.ref.bucket,
            key=o.ref.key,
            content_type=o.content_type,
            size=o.size,
            sha256=o.sha256,
            page_count=o.page_count,
        )
        for o in completion.outputs
    ]
    return StoredCompletion(
        outputs=outputs, usage_pages=completion.usage_pages, sequence=completion.sequence
    ).model_dump(mode="json")


def _completion(data: dict[str, Any], context: ItemContext) -> ConversionCompleted:
    stored = StoredCompletion.model_validate(data)
    outputs = tuple(
        StoredOutput(
            ref=ObjectRef(bucket=o.bucket, key=o.key),
            content_type=o.content_type,
            size=o.size,
            sha256=o.sha256,
            page_count=o.page_count,
        )
        for o in stored.outputs
    )
    return ConversionCompleted(item=context, outputs=outputs, usage_pages=stored.usage_pages, sequence=stored.sequence)
