"""The wire contract with document-service (IDP-00 ch. 7.6): what we read, and exactly what we send."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import (
    ExtractionCompleted,
    ItemContext,
    JobProgressed,
    ObjectRef,
    ResultSummary,
    WorkFailed,
)
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.infrastructure.messaging.mappers import event_id_of, to_accept_command, to_outbox_row
from extraction.pipeline.infrastructure.messaging.messages import EnvelopeMessage, ExtractionRequestedMessage

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
TENANT, JOB, ITEM, DOC = uuid4(), uuid4(), uuid4(), uuid4()


def _envelope(**changes: object) -> EnvelopeMessage:
    payload = {
        "jobId": str(JOB),
        "itemId": str(ITEM),
        "documentId": str(DOC),
        "files": [{"bucket": "idp-originals", "key": f"{TENANT}/{DOC}/file-1", "contentType": "image/jpeg"}],
        "templateId": "auto",
        "options": {},
        "accountPolicy": {"masking": "AADHAAR"},
        "futureField": "ignored",  # MSG-06
        **changes,
    }
    return EnvelopeMessage.model_validate(
        {
            "messageId": str(uuid4()),
            "type": "ExtractionRequested",
            "version": 1,
            "occurredAt": NOW.isoformat(),
            "producer": "document-service",
            "correlationId": "corr-7",
            "tenantId": str(TENANT),
            "subjectId": str(ITEM),
            "payload": payload,
        }
    )


def _command(envelope: EnvelopeMessage):
    return to_accept_command(envelope, ExtractionRequestedMessage.model_validate(envelope.payload), event_id=uuid4())


def test_maps_an_extraction_requested_message_to_a_command() -> None:
    request = _command(_envelope()).request

    assert (request.context.tenant_id, request.context.job_id, request.context.item_id) == (TENANT, JOB, ITEM)
    assert (request.document_id, request.template_id) == (DOC, None)  # "auto": classify
    assert request.files == (ObjectRef(bucket="idp-originals", key=f"{TENANT}/{DOC}/file-1"),)


def test_keeps_a_named_template() -> None:
    assert _command(_envelope(templateId="standard-pan-card")).request.template_id == "standard-pan-card"


@pytest.mark.parametrize(
    "change",
    [
        {"files": [{"bucket": "idp-originals", "key": "k/a/b", "contentType": "text/plain"}]},
        {"files": [{"bucket": "idp-originals", "key": "../escape", "contentType": "image/png"}]},
        {"files": [{"bucket": "idp-originals", "key": f"k/{n}", "contentType": "image/png"} for n in range(3)]},
    ],
)
def test_raises_a_domain_error_when_the_request_cannot_be_used(change: dict[str, object]) -> None:
    with pytest.raises(RequestRejectedError):
        _command(_envelope(**change))


def test_uses_the_event_id_header_as_the_duplicate_key_and_falls_back_to_the_message_id() -> None:
    envelope, header = _envelope(), uuid4()

    assert event_id_of({"eventId": str(header)}, envelope) == header
    assert event_id_of({}, envelope) == envelope.message_id
    with pytest.raises(RequestRejectedError):
        event_id_of({"eventId": "nope"}, envelope)


CONTEXT = ItemContext(tenant_id=TENANT, correlation_id="corr-7", job_id=JOB, item_id=ITEM)


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        (
            JobProgressed(item=CONTEXT, stage=Stage.READ, progress=100, sequence=5),
            {"jobId": str(JOB), "itemId": str(ITEM), "stage": "READ", "progress": 100, "sequence": 5},
        ),
        (
            ExtractionCompleted(
                item=CONTEXT,
                result=ObjectRef(bucket="idp-derived", key="a/d/i/result.json"),
                document_type="PAN_CARD",
                template_id="standard-pan-card",
                template_version=1,
                summary=ResultSummary(fields=4, needs_review=1, avg_confidence=0.91),
                usage_pages=1,
                usage_ai_calls=1,
                sequence=9,
            ),
            {
                "jobId": str(JOB),
                "itemId": str(ITEM),
                "resultKey": "a/d/i/result.json",
                "documentType": "PAN_CARD",
                "templateRef": {"id": "standard-pan-card", "version": 1},
                "summary": {"fields": 4, "needsReview": 1, "avgConfidence": 0.91},
                "usage": {"pages": 1, "aiCalls": 1},
            },
        ),
        (
            WorkFailed(item=CONTEXT, code="EXT-422-QUALITY-001", retryable=False, detail="blurred", sequence=3),  # type: ignore[arg-type]
            {
                "jobId": str(JOB),
                "itemId": str(ITEM),
                "error": {"code": "EXT-422-QUALITY-001", "retryable": False, "detail": "blurred"},
            },
        ),
    ],
)
def test_writes_the_exact_envelope_java_expects_into_the_outbox(event, payload) -> None:
    row = to_outbox_row(event, message_id=UUID(int=7), occurred_at=NOW, producer="extraction-service")

    assert row["payload"]["payload"] == payload
    assert (row["payload"]["type"], row["payload"]["producer"], row["payload"]["subjectId"]) == (
        type(event).__name__,
        "extraction-service",
        str(ITEM),
    )
    assert (row["ordering_key"], row["aggregate_version"], row["status"]) == (str(ITEM), event.sequence, "PENDING")


def test_marks_the_request_as_priority_when_it_came_on_the_priority_route() -> None:
    envelope = _envelope()
    payload = ExtractionRequestedMessage.model_validate(envelope.payload)

    command = to_accept_command(envelope, payload, event_id=uuid4(), priority=True)

    assert command.request.priority
