"""Wire format of the messages this service receives and sends (IDP-00 ch. 7.6; standard ch. 16.4).

``*Message`` classes stop at this package (OBJ-01). Incoming messages ignore unknown fields, so Java can add
fields within a version (MSG-06); outgoing ones are built only by ``mappers``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PRODUCER = "conversion-service"
EVENTS_EXCHANGE = "idp.events"
COMMANDS_EXCHANGE = "idp.commands"
EVENT_ID_HEADER = "eventId"  # Java's outbox row id; agreed as the duplicate key (2026-10-22)
EVENT_ROUTING_KEYS = {
    "JobProgressed": "job.progressed",
    "ConversionCompleted": "conversion.completed",
    "WorkFailed": "work.failed",
}
CONVERSION_REQUESTED = "ConversionRequested"


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


class InputFileMessage(_Incoming):
    bucket: str = Field(max_length=63)
    key: str = Field(max_length=1024)
    content_type: str = Field(max_length=255)
    pages: int | None = None


class ConversionRequestedMessage(_Incoming):
    job_id: UUID
    item_id: UUID
    type: str = Field(max_length=32)  # CONVERSION / PDF_EDIT / PDF_MERGE / EXPORT / PREVIEW
    inputs: list[InputFileMessage] = Field(min_length=1, max_length=500)
    target: str | None = Field(default=None, max_length=32)  # PDF_MERGE always makes a PDF; PREVIEW makes JPEGs
    options: dict[str, Any] = Field(default_factory=dict)
    edit_plan: dict[str, Any] | None = None  # PDF_EDIT: the plan here or in options.editPlan (IDP-00 ch. 7.6)
    output_prefix: str = Field(max_length=900)


class JobProgressedMessage(_Outgoing):
    job_id: UUID
    item_id: UUID
    stage: str
    progress: int
    sequence: int


class OutputFileMessage(_Outgoing):
    bucket: str
    key: str
    content_type: str
    size: int
    sha256: str
    page_count: int | None


class UsageMessage(_Outgoing):
    pages: int


class ConversionCompletedMessage(_Outgoing):
    job_id: UUID
    item_id: UUID
    outputs: list[OutputFileMessage]
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
    payload: JobProgressedMessage | ConversionCompletedMessage | WorkFailedMessage
