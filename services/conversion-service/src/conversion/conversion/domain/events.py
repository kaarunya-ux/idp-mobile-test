"""What conversion-service tells document-service about a job item (IDP-00 ch. 7.6), as domain facts.

They become outbox rows and then messages; ``infrastructure/messaging`` owns the wire format. ``sequence`` grows
with every event of an item, so Java can ignore progress that arrives late (IDP-02: monotonic progress).
"""

from dataclasses import dataclass
from uuid import UUID

from conversion.conversion.domain.values import Stage, StoredOutput
from conversion.core.error_codes import ErrorCode


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemContext:
    """Which item an event is about, and the ids that travel in its envelope."""

    tenant_id: UUID
    correlation_id: str
    job_id: UUID
    item_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class JobProgressed:
    item: ItemContext
    stage: Stage
    progress: int  # percent, 0-100
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversionCompleted:
    item: ItemContext
    outputs: tuple[StoredOutput, ...]
    usage_pages: int  # what Java charges to the user's quota
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkFailed:
    item: ItemContext
    code: ErrorCode
    retryable: bool
    detail: str  # safe to show users (ERR-06)
    sequence: int


type ConversionEvent = JobProgressed | ConversionCompleted | WorkFailed
