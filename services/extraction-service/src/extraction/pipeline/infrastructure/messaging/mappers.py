"""Messages <-> commands and events. The only place that knows both the wire format and the domain (OBJ-01)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from extraction.core.outbox import OutboxStatus
from extraction.pipeline.application.commands import AcceptExtractionCommand
from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import (
    DeepAnalysisSpec,
    ExtractionCompleted,
    ExtractionEvent,
    ExtractionRequest,
    ItemContext,
    JobKind,
    JobProgressed,
    ObjectRef,
    RecheckSpec,
    WorkFailed,
)
from extraction.pipeline.infrastructure.messaging.messages import (
    DEEP_ANALYSIS_REQUESTED,
    EVENT_ID_HEADER,
    EXTRACTION_REQUESTED,
    DeepAnalysisRequestedMessage,
    EnvelopeMessage,
    ErrorMessage,
    ExtractionCompletedMessage,
    ExtractionRequestedMessage,
    JobProgressedMessage,
    OutgoingEnvelopeMessage,
    RecheckOptionsMessage,
    SummaryMessage,
    TemplateRefMessage,
    UsageMessage,
    WorkFailedMessage,
)

_AGGREGATE_TYPE = "extraction_item"
_DERIVED_KEY_PARTS = 4
# What the workers read: PDFs, images, and Office documents (turned into PDFs). The real type is checked again (FIL-03).
_READABLE = (
    "application/pdf",
    "image/",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.oasis.opendocument.",
)


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
            raise RequestRejectedError(
                f"Files of type {file.content_type} can't be read; send a PDF, an image or an Office document."
            )
    template = payload.template_id if payload.template_id not in (None, "auto") else None
    recheck = _recheck(payload.options.get("recheck"), envelope.tenant_id)
    request = ExtractionRequest(
        context=item_context(envelope, payload),
        document_id=payload.document_id,
        files=tuple(ObjectRef(bucket=f.bucket, key=f.key) for f in payload.files),
        template_id=template,
        options={key: value for key, value in payload.options.items() if key != "recheck"},
        priority=priority,
        kind=JobKind.RECHECK if recheck else JobKind.EXTRACTION,
        recheck=recheck,
    )
    return AcceptExtractionCommand(message_id=event_id, message_type=EXTRACTION_REQUESTED, request=request)


def to_deep_analysis_command(
    envelope: EnvelopeMessage, payload: DeepAnalysisRequestedMessage, *, event_id: UUID, derived_bucket: str
) -> AcceptExtractionCommand:
    """The stored result names the document: ``<accountId>/<documentId>/<jobItemId>/result.json`` (IDP-00 ch. 7.7)."""
    tenant = envelope.tenant_id
    parts = payload.result_key.split("/")
    try:
        document_id = UUID(parts[1]) if len(parts) == _DERIVED_KEY_PARTS else None
    except ValueError:
        document_id = None
    if document_id is None:
        raise RequestRejectedError("resultKey is not <accountId>/<documentId>/<jobItemId>/result.json.")
    spec = DeepAnalysisSpec(
        result=_own(ObjectRef(bucket=derived_bucket, key=payload.result_key), tenant),
        pages=tuple(_own(ObjectRef(bucket=derived_bucket, key=key), tenant) for key in payload.page_keys),
        question=payload.question,
    )
    request = ExtractionRequest(
        context=ItemContext(
            tenant_id=tenant, correlation_id=envelope.correlation_id, job_id=payload.job_id, item_id=payload.item_id
        ),
        document_id=document_id,
        files=(),
        template_id=None,
        options={},
        kind=JobKind.DEEP_ANALYSIS,
        analysis=spec,
    )
    return AcceptExtractionCommand(message_id=event_id, message_type=DEEP_ANALYSIS_REQUESTED, request=request)


def _recheck(options: object, tenant: UUID) -> RecheckSpec | None:
    if options is None:
        return None
    try:
        found = RecheckOptionsMessage.model_validate(options)
    except ValidationError:
        raise RequestRejectedError("options.recheck needs result {bucket, key} and at most 50 fields.") from None
    return RecheckSpec(
        result=_own(ObjectRef(bucket=found.result.bucket, key=found.result.key), tenant), fields=tuple(found.fields)
    )


def _own(ref: ObjectRef, tenant: UUID) -> ObjectRef:
    """A key from a message must be the sender's account's (IDP-00 ch. 7.7), never another account's (SEC-05)."""
    if not ref.key.startswith(f"{tenant}/"):
        raise RequestRejectedError("An object key belongs to another account.")
    return ref


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
