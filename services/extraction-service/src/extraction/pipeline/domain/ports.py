"""What the first pipeline stages need from the outside, in domain language (ports, standard ch. 7)."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from extraction.pipeline.domain.jobs import ExtractionEvent, ExtractionItem, ObjectRef
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.template import TemplateDefinition
from idp_ocr_engines.model import OcrPage, PageImage


class PageLoaderPort(Protocol):
    """Checks a file and turns it into page images: PDF pages at 300 DPI, or the image's own pages."""

    def load_pages(self, content: bytes) -> tuple[PageImage, ...]:
        """Raises ``FileRejectedError`` or ``TooManyPagesError`` when the file cannot be accepted (stage SCAN)."""
        ...


class QualityAssessorPort(Protocol):
    """Measures how readable a page is."""

    def assess(self, image: PageImage) -> QualityScores: ...


class PagePreparerPort(Protocol):
    """Straightens and cleans a page for OCR."""

    def prepare(self, image: PageImage) -> PreparedPage: ...


class ProgressReporterPort(Protocol):
    """Tells the outside world how far a stage is (``JobProgressed`` messages are sent from the outbox in step 6)."""

    def report(self, stage: Stage, percent: int) -> None: ...


class TemplateCatalogPort(Protocol):
    """The published templates a job may use: the standard ones and its account's own (IDP-03 ch. 2.6 stage 4)."""

    def templates_for(self, account_id: UUID | None, template_id: str | None) -> Sequence[TemplateDefinition]:
        """With ``template_id``, only that template when the account may use it; otherwise all of them."""
        ...


class DigitalTextReaderPort(Protocol):
    """Reads a digital PDF's text layer (IDP-03 ch. 2.6 stage 5): no OCR for documents made by software."""

    def read_first_page(self, content: bytes) -> OcrPage | None:
        """The first page's words with positions, or None when it must be read with OCR (not a PDF, or a scan)."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class AiCallContext:
    """Who an AI call is for and where its pages are stored: ai-gateway reads images by key, uses the account for its
    daily cap and the priority for its queue (IDP-03 ch. 4.2). ``labels`` describe the fields to a generic prompt."""

    account_id: UUID
    priority: bool
    page_refs: tuple[ObjectRef, ...]  # the same pages as ``pages``, in the derived bucket
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class AiReading:
    """The values the AI read, and which model and prompt read them (the result's provenance, ML-04)."""

    values: Mapping[str, str | None]
    model_id: str
    prompt_version: str


class AiFieldReaderPort(Protocol):
    """The AI fallback (IDP-03 ch. 2.6 stage 7): reads a document type's fields from the page in one request.

    ai-gateway's ``field-read`` (``GatewayFieldReader``); for a laptop without the gateway, the model host directly
    (``VisionModelFieldReader``, ADR 0006). Raises ``AiUnavailableError`` when it can't answer: the fields then stay
    NEEDS_REVIEW, which is never a failure.
    """

    def read_fields(
        self, document_type: str, pages: Sequence[PageImage], keys: Sequence[str], context: AiCallContext
    ) -> AiReading:
        """The value of each key as printed, or None. ``pages`` are the document's sides (a card's front and back);
        the reader sees them together. Keys the reader doesn't know come back None."""
        ...


# --- Job items: storage, database, task queue (the plumbing conversion-service uses too) ------------------------


class ObjectStoragePort(Protocol):
    """MinIO through the S3 API."""

    def read_bytes(self, ref: ObjectRef) -> bytes:
        """The object. Raises ``InputMissingError`` when it is gone and ``TooLargeError`` above the size limit,
        checked before anything is downloaded (FIL-05)."""
        ...

    def write_bytes(self, ref: ObjectRef, data: bytes, content_type: str) -> None: ...


class PageImageStorePort(Protocol):
    """Saves a prepared page as an image, for reviewers and exports (``pages[].imageKey``, IDP-00 ch. 7.8)."""

    def save_page(self, ref: ObjectRef, page: PageImage) -> None: ...


class ExtractionItemRepositoryPort(Protocol):
    def get_for_update(self, item_id: UUID) -> ExtractionItem | None:
        """The item, locked until the unit of work ends; None if it was never stored."""
        ...

    def add(self, item: ExtractionItem) -> None: ...

    def save(self, item: ExtractionItem) -> None: ...


class UnitOfWorkPort(Protocol):
    """One database transaction: item changes, the inbox and outbox rows commit together (OBX-01, MSG-02)."""

    @property
    def items(self) -> ExtractionItemRepositoryPort: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...

    def record_message_once(self, message_id: UUID, message_type: str) -> bool:
        """``True`` the first time ``message_id`` is seen; ``False`` for a redelivery."""
        ...

    def add_event(self, event: ExtractionEvent) -> None:
        """Queue ``event`` in the outbox; it is published after the commit."""
        ...

    def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


class TaskQueuePort(Protocol):
    def enqueue(self, item_id: UUID) -> None:
        """Hand the item to a pipeline worker (``pipeline`` queue, IDP-03 ch. 2.2)."""
        ...
