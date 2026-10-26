"""Messages <-> commands and events. The only place that knows both the wire format and the domain (OBJ-01)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from conversion.conversion.application.commands import AcceptConversionCommand, RejectConversionCommand
from conversion.conversion.domain.events import (
    ConversionCompleted,
    ConversionEvent,
    ItemContext,
    JobProgressed,
    WorkFailed,
)
from conversion.conversion.domain.exceptions import InvalidRequestError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionRequest
from conversion.conversion.domain.options import resolve_target
from conversion.conversion.domain.values import InputFile, ObjectRef
from conversion.conversion.infrastructure.messaging.messages import (
    CONVERSION_REQUESTED,
    EVENT_ID_HEADER,
    ConversionCompletedMessage,
    ConversionRequestedMessage,
    EnvelopeMessage,
    ErrorMessage,
    JobProgressedMessage,
    OutgoingEnvelopeMessage,
    OutputFileMessage,
    UsageMessage,
    WorkFailedMessage,
)
from conversion.core.errors import DomainError
from conversion.core.outbox import OutboxStatus

SUPPORTED_JOB_TYPES = frozenset({"CONVERSION"})  # PDF_EDIT, PDF_MERGE, EXPORT and PREVIEW come in step 5
_AGGREGATE_TYPE = "conversion_item"


def event_id_of(headers: Mapping[str, Any], envelope: EnvelopeMessage) -> UUID:
    """The duplicate key: Java's ``eventId`` header, else the envelope's ``messageId`` (the same value in Java)."""
    header = headers.get(EVENT_ID_HEADER)
    if header is None:
        return envelope.message_id
    try:
        return UUID(str(header))
    except ValueError:
        raise InvalidRequestError(f"The {EVENT_ID_HEADER} header is not a UUID.") from None


def item_context(envelope: EnvelopeMessage, payload: ConversionRequestedMessage) -> ItemContext:
    return ItemContext(
        tenant_id=envelope.tenant_id,
        correlation_id=envelope.correlation_id,
        job_id=payload.job_id,
        item_id=payload.item_id,
    )


def to_accept_command(
    envelope: EnvelopeMessage, payload: ConversionRequestedMessage, *, event_id: UUID
) -> AcceptConversionCommand:
    """Raise ``DomainError`` for a request this service can't act on; the caller turns it into ``WorkFailed``."""
    if payload.type not in SUPPORTED_JOB_TYPES:
        raise InvalidRequestError(f"Job type {payload.type} is not supported yet.")
    inputs = []
    for file in payload.inputs:
        file_format = Format.from_content_type(file.content_type)
        if file_format is None:
            raise InvalidRequestError(f"Input type {file.content_type} is not supported.")
        inputs.append(InputFile(ref=ObjectRef(bucket=file.bucket, key=file.key), format=file_format))
    request = ConversionRequest(
        context=item_context(envelope, payload),
        inputs=tuple(inputs),
        target=resolve_target(payload.target, payload.options.get("imageFormat")),
        options=payload.options,
        output_prefix=payload.output_prefix,
    )
    return AcceptConversionCommand(message_id=event_id, message_type=CONVERSION_REQUESTED, request=request)


def to_reject_command(
    envelope: EnvelopeMessage, payload: ConversionRequestedMessage, *, event_id: UUID, error: DomainError
) -> RejectConversionCommand:
    return RejectConversionCommand(
        message_id=event_id, message_type=CONVERSION_REQUESTED, item=item_context(envelope, payload), error=error
    )


def to_outbox_row(event: ConversionEvent, *, message_id: UUID, occurred_at: datetime, producer: str) -> dict[str, Any]:
    """The ``outbox_event`` columns for ``event``; ``payload`` holds the whole envelope as it will be sent."""
    item = event.item
    envelope = OutgoingEnvelopeMessage(
        message_id=message_id,
        type=type(event).__name__,
        occurred_at=occurred_at,
        producer=producer,
        correlation_id=item.correlation_id,
        tenant_id=item.tenant_id,
        subject_id=str(item.item_id),
        payload=_payload(event),
    )
    return {
        "id": message_id,
        "tenant_id": item.tenant_id,
        "aggregate_type": _AGGREGATE_TYPE,
        "aggregate_id": item.item_id,
        "ordering_key": str(item.item_id),
        "aggregate_version": event.sequence,
        "event_type": envelope.type,
        "payload": envelope.model_dump(mode="json", by_alias=True),
        "status": OutboxStatus.PENDING,
        "attempts": 0,
        "available_at": occurred_at,
        "correlation_id": item.correlation_id,
        "created_at": occurred_at,
    }


def _payload(event: ConversionEvent) -> JobProgressedMessage | ConversionCompletedMessage | WorkFailedMessage:
    item = event.item
    match event:
        case JobProgressed():
            return JobProgressedMessage(
                job_id=item.job_id,
                item_id=item.item_id,
                stage=event.stage,
                progress=event.progress,
                sequence=event.sequence,
            )
        case ConversionCompleted():
            outputs = [
                OutputFileMessage(
                    bucket=o.ref.bucket,
                    key=o.ref.key,
                    content_type=o.content_type,
                    size=o.size,
                    sha256=o.sha256,
                    page_count=o.page_count,
                )
                for o in event.outputs
            ]
            return ConversionCompletedMessage(
                job_id=item.job_id, item_id=item.item_id, outputs=outputs, usage=UsageMessage(pages=event.usage_pages)
            )
        case WorkFailed():
            error = ErrorMessage(code=event.code, retryable=event.retryable, detail=event.detail)
            return WorkFailedMessage(job_id=item.job_id, item_id=item.item_id, error=error)
