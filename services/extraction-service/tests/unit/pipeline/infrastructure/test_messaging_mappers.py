"""The wire contract with document-service (IDP-00 ch. 7.6): what we read, and exactly what we send."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import (
    ExtractionCompleted,
    ItemContext,
    JobKind,
    JobProgressed,
    ObjectRef,
    ResultSummary,
    WorkFailed,
)
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.infrastructure.messaging.mappers import (
    event_id_of,
    to_accept_command,
    to_deep_analysis_command,
    to_outbox_row,
)
from extraction.pipeline.infrastructure.messaging.messages import (
    DeepAnalysisRequestedMessage,
    EnvelopeMessage,
    ExtractionRequestedMessage,
)

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


def test_maps_a_recheck_option_to_a_recheck_request() -> None:
    recheck = {"result": {"bucket": "idp-derived", "key": f"{TENANT}/{DOC}/device/result.json"}, "fields": ["name"]}

    request = _command(_envelope(options={"recheck": recheck, "language": ["en"]})).request

    assert request.kind is JobKind.RECHECK
    assert request.recheck is not None
    assert (request.recheck.result.key, request.recheck.fields) == (recheck["result"]["key"], ("name",))
    assert request.options == {"language": ["en"]}


@pytest.mark.parametrize(
    "recheck",
    [
        {"result": {"bucket": "idp-derived", "key": f"{uuid4()}/{DOC}/device/result.json"}},  # another account
        {"fields": ["name"]},  # no result
    ],
)
def test_refuses_a_recheck_of_another_accounts_result_or_without_one(recheck: dict) -> None:
    with pytest.raises(RequestRejectedError):
        _command(_envelope(options={"recheck": recheck}))


def _deep(**changes: object) -> tuple[EnvelopeMessage, DeepAnalysisRequestedMessage]:
    payload = {
        "jobId": str(JOB),
        "itemId": str(ITEM),
        "resultKey": f"{TENANT}/{DOC}/{uuid4()}/result.json",
        "pageKeys": [f"{TENANT}/{DOC}/first/page-0.png"],
        "question": "Is it valid?",
        **changes,
    }
    envelope = _envelope().model_copy(update={"type": "DeepAnalysisRequested", "payload": payload})
    return envelope, DeepAnalysisRequestedMessage.model_validate(payload)


def test_maps_deep_analysis_to_an_item_for_the_same_document() -> None:
    envelope, payload = _deep()

    command = to_deep_analysis_command(envelope, payload, event_id=uuid4(), derived_bucket="idp-derived")

    request = command.request
    assert (request.kind, request.document_id, request.files) == (JobKind.DEEP_ANALYSIS, DOC, ())
    assert request.analysis is not None
    assert request.analysis.question == "Is it valid?"
    assert request.analysis.pages[0].bucket == "idp-derived"
    assert command.message_type == "DeepAnalysisRequested"


@pytest.mark.parametrize(
    "changes",
    [
        {"resultKey": "not-a-derived-key"},
        {"resultKey": f"{TENANT}/not-a-uuid/item/result.json"},
        {"resultKey": f"{uuid4()}/{DOC}/item/result.json"},  # another account's result
        {"pageKeys": [f"{uuid4()}/{DOC}/first/page-0.png"]},  # another account's page
    ],
)
def test_refuses_deep_analysis_when_a_key_is_not_the_accounts_or_not_a_result(changes: dict) -> None:
    envelope, payload = _deep(**changes)

    with pytest.raises(RequestRejectedError):
        to_deep_analysis_command(envelope, payload, event_id=uuid4(), derived_bucket="idp-derived")


def test_accepts_word_and_excel_files() -> None:
    files = [
        {"bucket": "idp-originals", "key": f"{TENANT}/{DOC}/a", "contentType": ct}
        for ct in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
        )
    ]

    assert len(_command(_envelope(files=files)).request.files) == 2
