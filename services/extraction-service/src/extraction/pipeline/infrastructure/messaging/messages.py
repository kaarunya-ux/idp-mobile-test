"""Wire format of the messages this service receives and sends (IDP-00 ch. 7.6; standard ch. 16.4).

``*Message`` classes stop at this package (OBJ-01). Incoming messages ignore unknown fields, so Java can add
fields within a version (MSG-06); outgoing ones are built only by ``mappers``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PRODUCER = "extraction-service"
EVENTS_EXCHANGE = "idp.events"
COMMANDS_EXCHANGE = "idp.commands"
EVENT_ID_HEADER = "eventId"  # Java's outbox row id; the duplicate key, as agreed for conversion-service
EVENT_ROUTING_KEYS = {
    "JobProgressed": "job.progressed",
    "ExtractionCompleted": "extraction.completed",
    "WorkFailed": "work.failed",
}
EXTRACTION_REQUESTED = "ExtractionRequested"
DEEP_ANALYSIS_REQUESTED = "DeepAnalysisRequested"
ACCOUNT_DELETED = "AccountDeleted"


class _Incoming(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True, extra="ignore")


class _Outgoing(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid")


class EnvelopeMessage(_Incoming):
    """The envelope every IDP message has, identical in Java and Python (Java standard ch. 27.3)."""

    message_id: UUID
    type: str = Field(max_length=100)
    version: int = Field(ge=1)
    occurred_at: datetime
    producer: str = Field(max_length=100)
    correlation_id: str = Field(min_length=1, max_length=128)
    tenant_id: UUID
    subject_id: str = Field(max_length=128)
    payload: dict[str, Any]


class FileMessage(_Incoming):
    bucket: str = Field(max_length=63)
    key: str = Field(max_length=1024)
    content_type: str = Field(max_length=255)
    pages: int | None = None


class ItemRequestMessage(_Incoming):
    """What every work request names: its job and job item."""

    job_id: UUID
    item_id: UUID


class ExtractionRequestedMessage(ItemRequestMessage):
    """IDP-00 ch. 7.6: jobId, itemId, documentId, files[], templateId?/auto, options, accountPolicy, deadline."""

    document_id: UUID
    files: list[FileMessage] = Field(min_length=1, max_length=20)
    template_id: str | None = Field(default=None, max_length=64)  # absent or "auto": classify
    options: dict[str, Any] = Field(default_factory=dict)
    account_policy: dict[str, Any] = Field(default_factory=dict)  # masking: the standard templates always mask
    deadline: datetime | None = None  # Java's reaper enforces it; the worker has its own time limit


class RefMessage(_Incoming):
    bucket: str = Field(max_length=63)
    key: str = Field(max_length=1024)


class RecheckOptionsMessage(_Incoming):
    """``options.recheck`` of an ``ExtractionRequested`` that is a RECHECK job (ADR 0007: proposed to Java)."""

    result: RefMessage  # the on-device result JSON
    fields: list[str] = Field(default_factory=list, max_length=50)


class DeepAnalysisRequestedMessage(ItemRequestMessage):
    """IDP-00 ch. 7.6: jobId, itemId, resultKey, pageKeys, question?. Keys are in the derived bucket."""

    result_key: str = Field(max_length=1024)
    page_keys: list[str] = Field(min_length=1, max_length=5)
    question: str | None = Field(default=None, max_length=500)


class AccountDeletedMessage(_Incoming):
    account_id: UUID
    delete_after: datetime | None = None


class JobProgressedMessage(_Outgoing):
    job_id: UUID
    item_id: UUID
    stage: str
    progress: int
    sequence: int


class TemplateRefMessage(_Outgoing):
    id: str
    version: int | None


class SummaryMessage(_Outgoing):
    fields: int
    needs_review: int
    avg_confidence: float


class UsageMessage(_Outgoing):
    pages: int
    ai_calls: int


class ExtractionCompletedMessage(_Outgoing):
    job_id: UUID
    item_id: UUID
    result_key: str  # in the derived bucket
    document_type: str | None
    template_ref: TemplateRefMessage | None
    summary: SummaryMessage
    usage: UsageMessage


class ErrorMessage(_Outgoing):
    code: str
    retryable: bool
    detail: str


class WorkFailedMessage(_Outgoing):
    job_id: UUID
    item_id: UUID
    error: ErrorMessage


class OutgoingEnvelopeMessage(_Outgoing):
    message_id: UUID
    type: str
    version: int = 1
    occurred_at: datetime
    producer: str = PRODUCER
    correlation_id: str
    tenant_id: UUID
    subject_id: str
    payload: JobProgressedMessage | ExtractionCompletedMessage | WorkFailedMessage
