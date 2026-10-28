"""Inputs of the pipeline use cases (standard ch. 1: commands are frozen dataclasses)."""

from dataclasses import dataclass
from uuid import UUID

from extraction.core.errors import DomainError
from extraction.pipeline.domain.jobs import ExtractionRequest, ItemContext
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareDocumentCommand:
    """The bytes of one uploaded file. The worker reads them from object storage before calling the service."""

    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractDocumentCommand:
    """Pages already prepared (PREPARE) with the quality measured on each. Standard templates read page 0."""

    pages: tuple[PreparedPage, ...]
    qualities: tuple[QualityScores, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptExtractionCommand:
    """An ``ExtractionRequested`` message, checked. ``message_id`` is its ``eventId``: the inbox key."""

    message_id: UUID
    message_type: str
    request: ExtractionRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectExtractionCommand:
    """A readable message this service can't act on: Java is told with ``WorkFailed`` so the item doesn't hang."""

    message_id: UUID
    message_type: str
    item: ItemContext
    error: DomainError


@dataclass(frozen=True, slots=True, kw_only=True)
class RunExtractionCommand:
    item_id: UUID
