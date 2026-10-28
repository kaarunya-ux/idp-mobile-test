"""Messages <-> commands and events. The only place that knows both the wire format and the domain (OBJ-01)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from extraction.core.errors import DomainError
from extraction.core.outbox import OutboxStatus
from extraction.pipeline.application.commands import AcceptExtractionCommand, RejectExtractionCommand
from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import (
    ExtractionCompleted,
    ExtractionEvent,
    ExtractionRequest,
    ItemContext,
    JobProgressed,
    ObjectRef,
    WorkFailed,
)
from extraction.pipeline.infrastructure.messaging.messages import (
    EVENT_ID_HEADER,
    EXTRACTION_REQUESTED,
    EnvelopeMessage,
    ErrorMessage,
    ExtractionCompletedMessage,
    ExtractionRequestedMessage,
    JobProgressedMessage,
    OutgoingEnvelopeMessage,
    SummaryMessage,
    TemplateRefMessage,
    UsageMessage,
    WorkFailedMessage,
)

_AGGREGATE_TYPE = "extraction_item"
_READABLE = ("application/pdf", "image/")  # what the page loader takes; it checks the real type again (FIL-03)


def event_id_of(headers: Mapping[str, Any], envelope: EnvelopeMessage) -> UUID:
    """The duplicate key: Java's ``eventId`` header, else the envelope's ``messageId`` (the same value in Java)."""
    header = headers.get(EVENT_ID_HEADER)
    if header is None:
        return envelope.message_id
    try:
        return UUID(str(header))
    except ValueError:
        raise RequestRejectedError(f"The {EVENT_ID_HEADER} header is not a UUID.") from None


def item_context(envelope: EnvelopeMessage, payload: ExtractionRequestedMessage) -> ItemContext:
    return ItemContext(
        tenant_id=envelope.tenant_id,
        correlation_id=envelope.correlation_id,
        job_id=payload.job_id,
        item_id=payload.item_id,
    )


def to_accept_command(
    envelope: EnvelopeMessage, payload: ExtractionRequestedMessage, *, event_id: UUID, priority: bool = False
) -> AcceptExtractionCommand:
    """Raise ``DomainError`` for a request this service can't act on; the caller turns it into ``WorkFailed``."""
    for file in payload.files:
        if not file.content_type.lower().startswith(_READABLE):
            raise RequestRejectedError(f"Files of type {file.content_type} can't be read; send a PDF or an image.")
    template = payload.template_id if payload.template_id not in (None, "auto") else None
    request = ExtractionRequest(
        context=item_context(envelope, payload),
        document_id=payload.document_id,
        files=tuple(ObjectRef(bucket=f.bucket, key=f.key) for f in payload.files),
        template_id=template,
        options=payload.options,
        priority=priority,
    )
    return AcceptExtractionCommand(message_id=event_id, message_type=EXTRACTION_REQUESTED, request=request)


def to_reject_command(
    envelope: EnvelopeMessage, payload: ExtractionRequestedMessage, *, event_id: UUID, error: DomainError
) -> RejectExtractionCommand:
    return RejectExtractionCommand(
        message_id=event_id, message_type=EXTRACTION_REQUESTED, item=item_context(envelope, payload), error=error
    )


def to_outbox_row(event: ExtractionEvent, *, message_id: UUID, occurred_at: datetime, producer: str) -> dict[str, Any]:
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


def _payload(event: ExtractionEvent) -> JobProgressedMessage | ExtractionCompletedMessage | WorkFailedMessage:
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
        case ExtractionCompleted():
            template = (
                TemplateRefMessage(id=event.template_id, version=event.template_version) if event.template_id else None
            )
            return ExtractionCompletedMessage(
                job_id=item.job_id,
                item_id=item.item_id,
                result_key=event.result.key,
                document_type=event.document_type,
                template_ref=template,
                summary=SummaryMessage(
                    fields=event.summary.fields,
                    needs_review=event.summary.needs_review,
                    avg_confidence=event.summary.avg_confidence,
                ),
                usage=UsageMessage(pages=event.usage_pages, ai_calls=event.usage_ai_calls),
            )
        case WorkFailed():
            return WorkFailedMessage(
                job_id=item.job_id,
                item_id=item.item_id,
                error=ErrorMessage(code=event.code, retryable=event.retryable, detail=event.detail),
            )
