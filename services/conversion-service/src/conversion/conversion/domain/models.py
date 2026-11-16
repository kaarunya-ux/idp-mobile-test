"""A job item as conversion-service tracks it: the request, where it is, and how it ended (CEL-03, MSG-02).

The rules for a request that arrives again (agreed with the Java backend, 2026-10-22):

=================  =====================================================================================
Item is            A new request for it (a new ``eventId``) means
=================  =====================================================================================
not known          start it
QUEUED             make sure it is queued (a crash may have lost the task)
RUNNING, recent    nothing: a worker has it
RUNNING, stale     its worker died; queue it again
COMPLETED          don't convert again; send the stored ``ConversionCompleted`` again (Java may have missed it)
FAILED             Java is retrying on purpose; queue it again
=================  =====================================================================================

The same ``eventId`` twice is a redelivery and is stopped by the inbox before these rules apply.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from conversion.conversion.domain.events import ConversionCompleted, ItemContext, JobProgressed, WorkFailed
from conversion.conversion.domain.exceptions import InvalidRequestError
from conversion.conversion.domain.formats import EXPORT_FORMATS, Format
from conversion.conversion.domain.values import InputFile, JobType, Stage, StoredOutput
from conversion.core.error_codes import ErrorCode

MAX_INPUTS = 200  # images into one PDF or deck; IDP-02 caps batches below this
_UNSAFE_PREFIX = ("/", "..", "\\")


class ItemStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IntakeAction(StrEnum):
    """What the consumer does after recording a request."""

    ENQUEUE = "ENQUEUE"
    RESEND_COMPLETION = "RESEND_COMPLETION"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversionRequest:
    """One ``ConversionRequested`` job item, checked. ``options`` stays as Java sent it; ``parse_job_options``
    types it when the item runs, so the stored request is exactly what was asked."""

    context: ItemContext
    inputs: tuple[InputFile, ...]
    target: Format
    options: Mapping[str, object]
    output_prefix: str  # key prefix: <accountId>/<jobId>/<itemId>, or <accountId>/<documentId>/<itemId> for PREVIEW
    job_type: JobType = JobType.CONVERSION

    def __post_init__(self) -> None:
        if not 1 <= len(self.inputs) <= MAX_INPUTS:
            raise InvalidRequestError(f"A job item needs 1 to {MAX_INPUTS} input files.")
        self._check_inputs_for_job_type()
        prefix = self.output_prefix.strip("/")
        if not prefix or prefix.startswith(_UNSAFE_PREFIX) or ".." in prefix or "\\" in prefix:
            raise InvalidRequestError("The output prefix is empty or not a safe relative path.")

    def _check_inputs_for_job_type(self) -> None:
        match self.job_type:
            case JobType.CONVERSION if len(self.inputs) > 1 and not all(i.format.is_image for i in self.inputs):
                raise InvalidRequestError("Only images can be combined; other files are converted one at a time.")
            case JobType.PDF_MERGE if not all(i.format is Format.PDF or i.format.is_image for i in self.inputs):
                raise InvalidRequestError("Only PDFs and images can be merged.")
            case JobType.PDF_MERGE if self.target is not Format.PDF:
                raise InvalidRequestError("A merge always makes a PDF.")
            case JobType.PREVIEW if len(self.inputs) != 1:
                raise InvalidRequestError("A preview is made for one document at a time.")
            case JobType.PREVIEW if not self.target.is_image:
                raise InvalidRequestError("Previews are images.")
            case JobType.PDF_EDIT if not all(i.format is Format.PDF for i in self.inputs):
                raise InvalidRequestError("An edit takes PDFs: the document, and any whose pages it inserts.")
            case JobType.PDF_EDIT if self.target is not Format.PDF:
                raise InvalidRequestError("An edit always makes a PDF.")
            case JobType.EXPORT if not all(i.format is Format.JSON for i in self.inputs):
                raise InvalidRequestError("An export takes extraction results (JSON).")
            case JobType.EXPORT if self.target not in EXPORT_FORMATS:
                raise InvalidRequestError(f"Results can't be exported as {self.target}.")

    @property
    def source(self) -> Format:
        return self.inputs[0].format

    def output_key(self, file_name: str) -> str:
        return f"{self.output_prefix.strip('/')}/{file_name}"


@dataclass(slots=True, kw_only=True)
class ConversionItem:
    """Mutable on purpose: each transition records the change and returns the event to send (DOM-01)."""

    request: ConversionRequest
    status: ItemStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0  # times a worker started it
    sequence: int = 0  # last event sequence
    completion: ConversionCompleted | None = None
    error_code: ErrorCode | None = None

    @classmethod
    def receive(cls, request: ConversionRequest, now: datetime) -> "ConversionItem":
        return cls(request=request, status=ItemStatus.QUEUED, updated_at=now, created_at=now)

    @property
    def id(self) -> UUID:
        return self.request.context.item_id

    def on_new_request(self, request: ConversionRequest, now: datetime, *, stale_after: timedelta) -> IntakeAction:
        """Apply the table in the module docstring."""
        match self.status:
            case ItemStatus.COMPLETED:
                return IntakeAction.RESEND_COMPLETION
            case ItemStatus.QUEUED:
                return IntakeAction.ENQUEUE
            case ItemStatus.RUNNING if not self.is_stale(now, stale_after):
                return IntakeAction.SKIP
            case _:  # FAILED, or RUNNING on a worker that died
                self.request, self.status, self.error_code, self.updated_at = request, ItemStatus.QUEUED, None, now
                return IntakeAction.ENQUEUE

    def can_start(self, now: datetime, *, stale_after: timedelta) -> bool:
        return self.status is ItemStatus.QUEUED or (
            self.status is ItemStatus.RUNNING and self.is_stale(now, stale_after)
        )

    def is_stale(self, now: datetime, stale_after: timedelta) -> bool:
        return now - self.updated_at > stale_after

    def start(self, now: datetime) -> JobProgressed:
        self.status, self.attempts = ItemStatus.RUNNING, self.attempts + 1
        return self.progress(Stage.PREPARE, 0, now)

    def progress(self, stage: Stage, percent: int, now: datetime) -> JobProgressed:
        self.sequence, self.updated_at = self.sequence + 1, now
        return JobProgressed(item=self.request.context, stage=stage, progress=percent, sequence=self.sequence)

    def requeue(self, now: datetime) -> None:
        """A dependency failed mid-run and the task will retry: let the retry start it again."""
        if self.status is ItemStatus.RUNNING:
            self.status, self.updated_at = ItemStatus.QUEUED, now

    def complete(self, outputs: tuple[StoredOutput, ...], usage_pages: int, now: datetime) -> ConversionCompleted:
        self.sequence, self.updated_at, self.status = self.sequence + 1, now, ItemStatus.COMPLETED
        self.completion = ConversionCompleted(
            item=self.request.context, outputs=outputs, usage_pages=usage_pages, sequence=self.sequence
        )
        return self.completion

    def fail(self, code: ErrorCode, *, retryable: bool, detail: str, now: datetime) -> WorkFailed:
        self.sequence, self.updated_at, self.status, self.error_code = self.sequence + 1, now, ItemStatus.FAILED, code
        return WorkFailed(
            item=self.request.context, code=code, retryable=retryable, detail=detail, sequence=self.sequence
        )

    def stored_completion(self) -> ConversionCompleted:
        if self.completion is None:  # a COMPLETED item always has one; reaching this is a bug, not a user error
            raise ValueError(f"item {self.id} is COMPLETED without a stored result")
        return self.completion
