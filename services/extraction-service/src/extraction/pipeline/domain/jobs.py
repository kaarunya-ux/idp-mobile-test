"""A job item as extraction-service tracks it: the request, where it is, how it ended, and what it tells Java.

The rules for a request that arrives again are conversion-service's, agreed with the Java backend (2026-10-22):

=================  =====================================================================================
Item is            A new request for it (a new ``eventId``) means
=================  =====================================================================================
not known          start it
QUEUED             make sure it is queued (a crash may have lost the task)
RUNNING, recent    nothing: a worker has it
RUNNING, stale     its worker died; queue it again
COMPLETED          don't extract again; send the stored ``ExtractionCompleted`` again
FAILED             Java is retrying on purpose; queue it again
=================  =====================================================================================

The same ``eventId`` twice is a redelivery and is stopped by the inbox before these rules apply. Events carry a
``sequence`` that grows with every event of an item, so Java can ignore progress that arrives late.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from extraction.core.error_codes import ErrorCode
from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.stages import Stage

MAX_FILES = 2  # a card's front and back (IDP-00 ch. 7.6: files[]); multi-file documents come with templates
MAX_KEY_LENGTH = 1024
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_UNSAFE_KEY = re.compile(r"(^/)|(\.\.)|(\\)|[\x00-\x1f\x7f]")  # absolute, parent, backslash or control character


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectRef:
    """Where a file lives in object storage. Keys come from Java, so they are checked before any use (FIL-06)."""

    bucket: str
    key: str

    def __post_init__(self) -> None:
        if not _BUCKET.match(self.bucket):
            raise RequestRejectedError(f"'{self.bucket}' is not a valid bucket name.")
        if not 1 <= len(self.key) <= MAX_KEY_LENGTH or _UNSAFE_KEY.search(self.key):
            raise RequestRejectedError("An object key is empty, too long or not a safe relative path.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemContext:
    """Which item an event is about, and the ids that travel in its envelope."""

    tenant_id: UUID  # the account id
    correlation_id: str
    job_id: UUID
    item_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractionRequest:
    """One ``ExtractionRequested`` job item, checked. ``options`` stays as Java sent it."""

    context: ItemContext
    document_id: UUID
    files: tuple[ObjectRef, ...]  # the document; a second file is the back of a card
    template_id: str | None  # None: classify against the standard templates ("auto")
    options: Mapping[str, object]

    def __post_init__(self) -> None:
        if not 1 <= len(self.files) <= MAX_FILES:
            raise RequestRejectedError(
                f"An extraction takes 1 to {MAX_FILES} files (a document, or a card's two sides)."
            )

    def derived_key(self, name: str) -> str:
        """``<accountId>/<documentId>/<jobItemId>/<name>`` in the derived bucket (IDP-00 ch. 7.7)."""
        return f"{self.context.tenant_id}/{self.document_id}/{self.context.item_id}/{name}"


# --- Events ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class JobProgressed:
    item: ItemContext
    stage: Stage
    progress: int  # percent, 0-100
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultSummary:
    """``ExtractionCompleted.summary`` (IDP-00 ch. 7.6)."""

    fields: int
    needs_review: int
    avg_confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractionCompleted:
    item: ItemContext
    result: ObjectRef  # result.json in the derived bucket
    document_type: str | None
    template_id: str | None
    template_version: int | None
    summary: ResultSummary
    usage_pages: int
    usage_ai_calls: int
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkFailed:
    item: ItemContext
    code: ErrorCode
    retryable: bool
    detail: str  # safe to show users (ERR-06)
    sequence: int


type ExtractionEvent = JobProgressed | ExtractionCompleted | WorkFailed


# --- The item -------------------------------------------------------------------------------------------------


class ItemStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IntakeAction(StrEnum):
    ENQUEUE = "ENQUEUE"
    RESEND_COMPLETION = "RESEND_COMPLETION"
    SKIP = "SKIP"


@dataclass(slots=True, kw_only=True)
class ExtractionItem:
    """Mutable on purpose: each transition records the change and returns the event to send (DOM-01)."""

    request: ExtractionRequest
    status: ItemStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    sequence: int = 0
    completion: ExtractionCompleted | None = None
    error_code: ErrorCode | None = None

    @classmethod
    def receive(cls, request: ExtractionRequest, now: datetime) -> "ExtractionItem":
        return cls(request=request, status=ItemStatus.QUEUED, created_at=now, updated_at=now)

    @property
    def id(self) -> UUID:
        return self.request.context.item_id

    def on_new_request(self, request: ExtractionRequest, now: datetime, *, stale_after: timedelta) -> IntakeAction:
        """Apply the table in the module docstring."""
        match self.status:
            case ItemStatus.COMPLETED:
                return IntakeAction.RESEND_COMPLETION
            case ItemStatus.QUEUED:
                return IntakeAction.ENQUEUE
            case ItemStatus.RUNNING if not self.is_stale(now, stale_after):
                return IntakeAction.SKIP
            case _:
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
        return self.progress(Stage.SCAN, 0, now)

    def progress(self, stage: Stage, percent: int, now: datetime) -> JobProgressed:
        self.sequence, self.updated_at = self.sequence + 1, now
        return JobProgressed(item=self.request.context, stage=stage, progress=percent, sequence=self.sequence)

    def requeue(self, now: datetime) -> None:
        """A dependency failed mid-run and the task will retry: let the retry start it again."""
        if self.status is ItemStatus.RUNNING:
            self.status, self.updated_at = ItemStatus.QUEUED, now

    def complete(self, outcome: "ExtractionOutcome", now: datetime) -> ExtractionCompleted:
        self.sequence, self.updated_at, self.status = self.sequence + 1, now, ItemStatus.COMPLETED
        self.completion = ExtractionCompleted(
            item=self.request.context,
            result=outcome.result,
            document_type=outcome.document_type,
            template_id=outcome.template_id,
            template_version=outcome.template_version,
            summary=outcome.summary,
            usage_pages=outcome.pages,
            usage_ai_calls=outcome.ai_calls,
            sequence=self.sequence,
        )
        return self.completion

    def fail(self, code: ErrorCode, *, retryable: bool, detail: str, now: datetime) -> WorkFailed:
        self.sequence, self.updated_at, self.status, self.error_code = self.sequence + 1, now, ItemStatus.FAILED, code
        return WorkFailed(
            item=self.request.context, code=code, retryable=retryable, detail=detail, sequence=self.sequence
        )

    def stored_completion(self) -> ExtractionCompleted:
        if self.completion is None:
            raise ValueError("A COMPLETED item without a stored result")  # a broken invariant, not a user error
        return self.completion


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractionOutcome:
    """What a finished run produced, before it is recorded on the item."""

    result: ObjectRef
    document_type: str | None
    template_id: str | None
    template_version: int | None
    summary: ResultSummary
    pages: int
    ai_calls: int
