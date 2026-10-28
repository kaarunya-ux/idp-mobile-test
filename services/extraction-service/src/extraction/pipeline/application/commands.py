"""Inputs of the pipeline use cases (standard ch. 1: commands are frozen dataclasses)."""

from dataclasses import dataclass
from uuid import UUID

from extraction.core.errors import DomainError
from extraction.pipeline.domain.jobs import ExtractionRequest, ItemContext
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import AiCallContext
from extraction.pipeline.domain.quality import QualityScores
from idp_ocr_engines.model import OcrPage


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareDocumentCommand:
    """The bytes of one uploaded file. The worker reads them from object storage before calling the service."""

    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractDocumentCommand:
    """Pages already prepared (PREPARE) with the quality measured on each. Standard templates read page 0."""

    pages: tuple[PreparedPage, ...]
    qualities: tuple[QualityScores, ...]
    digital_page: OcrPage | None = None  # page 0's text layer, when the document is a digital PDF (no OCR then)
    ai_context: AiCallContext | None = None  # without it the AI step is skipped (fields stay for review)
    template_forced: bool = False  # the request named its template: use it without classifying (stage 4)


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
