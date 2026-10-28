"""What the first pipeline stages need from the outside, in domain language (ports, standard ch. 7)."""

from collections.abc import Callable, Mapping, Sequence
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from extraction.pipeline.domain.jobs import ExtractionEvent, ExtractionItem, ObjectRef
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from idp_ocr_engines.model import PageImage


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


class AiFieldReaderPort(Protocol):
    """The AI fallback (IDP-03 ch. 2.6 stage 7): reads a document type's fields from the page in one request.

    Today a vision model on the model host; once ai-gateway exists, its ``field-read`` endpoint. Raises
    ``AiUnavailableError`` when it can't answer: the fields then stay NEEDS_REVIEW, which is never a failure.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def read_fields(self, document_type: str, page: PageImage, keys: Sequence[str]) -> Mapping[str, str | None]:
        """The value of each key as printed, or None. Keys the reader doesn't know come back None."""
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
