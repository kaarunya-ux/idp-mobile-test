"""The wire contract with document-service (IDP-00 ch. 7.6): what we read, and exactly what we send."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from conversion.conversion.domain.events import ConversionCompleted, ItemContext, JobProgressed, WorkFailed
from conversion.conversion.domain.exceptions import InvalidOptionError, InvalidRequestError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.values import JobType, ObjectRef, Stage, StoredOutput
from conversion.conversion.infrastructure.messaging.mappers import (
    event_id_of,
    to_accept_command,
    to_outbox_row,
)
from conversion.conversion.infrastructure.messaging.messages import ConversionRequestedMessage, EnvelopeMessage
from conversion.core.error_codes import ErrorCode

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
TENANT, JOB, ITEM = uuid4(), uuid4(), uuid4()


def _envelope(**payload_changes: object) -> EnvelopeMessage:
    payload = {
        "jobId": str(JOB),
        "itemId": str(ITEM),
        "type": "CONVERSION",
        "inputs": [{"bucket": "idp-originals", "key": "acc/doc/file", "contentType": "application/pdf", "pages": 3}],
        "target": "IMAGE",
        "options": {"imageFormat": "JPEG", "dpi": 200},
        "outputPrefix": "acc/job/item",
        "futureField": "ignored",  # MSG-06: unknown fields are ignored
        **payload_changes,
    }
    return EnvelopeMessage.model_validate(
        {
            "messageId": str(uuid4()),
            "type": "ConversionRequested",
            "version": 1,
            "occurredAt": NOW.isoformat(),
            "producer": "document-service",
            "correlationId": "corr-42",
            "tenantId": str(TENANT),
            "subjectId": str(ITEM),
            "payload": payload,
        }
    )


def _command(envelope: EnvelopeMessage):
    payload = ConversionRequestedMessage.model_validate(envelope.payload)
    return to_accept_command(envelope, payload, event_id=envelope.message_id)


def test_maps_a_conversion_requested_message_to_a_command() -> None:
    envelope = _envelope()

    command = _command(envelope)

    request = command.request
    assert (command.message_id, command.message_type) == (envelope.message_id, "ConversionRequested")
    assert (request.context.tenant_id, request.context.job_id, request.context.item_id) == (TENANT, JOB, ITEM)
    assert request.context.correlation_id == "corr-42"
    assert (request.source, request.target) == (Format.PDF, Format.JPEG)
    assert request.inputs[0].ref == ObjectRef(bucket="idp-originals", key="acc/doc/file")
    assert request.options == {"imageFormat": "JPEG", "dpi": 200}


@pytest.mark.parametrize(
    ("job_type", "target", "expected"),
    [("PDF_MERGE", None, Format.PDF), ("PDF_MERGE", "PDF", Format.PDF), ("PREVIEW", None, Format.JPEG)],
)
def test_sets_the_target_from_the_job_type_when_the_job_is_a_merge_or_preview(
    job_type: str, target: str | None, expected: Format
) -> None:
    inputs = [{"bucket": "idp-originals", "key": f"acc/doc/{n}", "contentType": "application/pdf"} for n in range(2)]
    if job_type == "PREVIEW":
        inputs = inputs[:1]

    request = _command(_envelope(type=job_type, target=target, inputs=inputs, options={})).request

    assert (request.job_type, request.target) == (JobType(job_type), expected)


def test_stores_a_top_level_edit_plan_with_the_options_when_the_job_is_an_edit() -> None:
    plan = {"schemaVersion": 1, "operations": []}

    request = _command(_envelope(type="PDF_EDIT", target=None, editPlan=plan, options={"output": "SINGLE"})).request

    assert (request.job_type, request.target) == (JobType.PDF_EDIT, Format.PDF)
    assert request.options == {"output": "SINGLE", "editPlan": plan}


def test_reads_the_export_format_from_the_target_when_the_job_is_an_export() -> None:
    inputs = [{"bucket": "idp-derived", "key": "acc/doc/item/result.json", "contentType": "application/json"}]

    request = _command(_envelope(type="EXPORT", target="xlsx", inputs=inputs, options={})).request

    assert (request.job_type, request.target, request.source) == (JobType.EXPORT, Format.XLSX, Format.JSON)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"type": "TRANSLATE"}, InvalidRequestError),  # not a job type
        ({"type": "CONVERSION", "target": None}, InvalidRequestError),
        ({"type": "EXPORT", "target": None}, InvalidRequestError),
        ({"inputs": [{"bucket": "idp-originals", "key": "k", "contentType": "text/html"}]}, InvalidRequestError),
        ({"target": "ODT"}, InvalidOptionError),
        ({"outputPrefix": "../other-tenant"}, InvalidRequestError),
    ],
)
def test_raises_a_domain_error_when_the_request_cannot_be_used(change: dict[str, object], error: type) -> None:
    with pytest.raises(error):
        _command(_envelope(**change))


def test_uses_the_event_id_header_as_the_duplicate_key_and_falls_back_to_the_message_id() -> None:
    envelope, header = _envelope(), uuid4()

    assert event_id_of({"eventId": str(header)}, envelope) == header
    assert event_id_of({}, envelope) == envelope.message_id
    with pytest.raises(InvalidRequestError, match="eventId"):
        event_id_of({"eventId": "not-a-uuid"}, envelope)


CONTEXT = ItemContext(tenant_id=TENANT, correlation_id="corr-42", job_id=JOB, item_id=ITEM)
OUTPUT = StoredOutput(
    ref=ObjectRef(bucket="idp-outputs", key="acc/job/item/document.pdf"),
    content_type="application/pdf",
    size=1234,
    sha256="ab" * 32,
    page_count=3,
)


@pytest.mark.parametrize(
    ("event", "routing_payload"),
    [
        (
            JobProgressed(item=CONTEXT, stage=Stage.CONVERT, progress=30, sequence=2),
            {"jobId": str(JOB), "itemId": str(ITEM), "stage": "CONVERT", "progress": 30, "sequence": 2},
        ),
        (
            ConversionCompleted(item=CONTEXT, outputs=(OUTPUT,), usage_pages=3, sequence=4),
            {
                "jobId": str(JOB),
                "itemId": str(ITEM),
                "outputs": [
                    {
                        "bucket": "idp-outputs",
                        "key": "acc/job/item/document.pdf",
                        "contentType": "application/pdf",
                        "size": 1234,
                        "sha256": "ab" * 32,
                        "pageCount": 3,
                    }
                ],
                "usage": {"pages": 3},
            },
        ),
        (
            WorkFailed(item=CONTEXT, code=ErrorCode.PDF_CORRUPT, retryable=False, detail="damaged", sequence=2),
            {
                "jobId": str(JOB),
                "itemId": str(ITEM),
                "error": {"code": "CNV-422-PDF-002", "retryable": False, "detail": "damaged"},
            },
        ),
    ],
)
def test_writes_the_exact_envelope_java_expects_into_the_outbox(event, routing_payload) -> None:
    message_id = UUID(int=7)

    row = to_outbox_row(event, message_id=message_id, occurred_at=NOW, producer="conversion-service")

    assert row["payload"] == {
        "messageId": str(message_id),
        "type": type(event).__name__,
        "version": 1,
        "occurredAt": "2026-10-26T09:00:00Z",
        "producer": "conversion-service",
        "correlationId": "corr-42",
        "tenantId": str(TENANT),
        "subjectId": str(ITEM),
        "payload": routing_payload,
    }
    assert (row["id"], row["ordering_key"], row["aggregate_version"]) == (message_id, str(ITEM), event.sequence)
    assert (row["event_type"], row["status"], row["tenant_id"]) == (type(event).__name__, "PENDING", TENANT)
