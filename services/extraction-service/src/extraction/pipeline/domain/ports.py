"""What the first pipeline stages need from the outside, in domain language (ports, standard ch. 7)."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from extraction.pipeline.domain.jobs import ExtractionEvent, ExtractionItem, JobKind, ObjectRef
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.runs import PipelineRun
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


class OfficeConverterPort(Protocol):
    """Word, Excel, PowerPoint and OpenDocument files become a PDF, so the digital-PDF path reads them with exact
    positions and no OCR (IDP-03 ch. 2.6 stage 5)."""

    def to_pdf(self, content: bytes) -> bytes | None:
        """The PDF, or None when ``content`` isn't an Office document (PDFs and images pass through unchanged).
        Raises ``FileRejectedError`` when the document can't be opened."""
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


@dataclass(frozen=True, slots=True, kw_only=True)
class AiClassification:
    document_type: str | None  # one of the candidates, or None
    confidence: float
    model_id: str
    prompt_version: str


class AiClassifierPort(Protocol):
    """ai-gateway's ``classify``: which candidate type a page shows, when the keywords found none (stage 4)."""

    def classify(self, page: ObjectRef, candidates: Sequence[str], context: AiCallContext) -> AiClassification:
        """Raises ``AiUnavailableError`` when it can't answer; the document then stays unknown."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class AiAnalysis:
    """Deep analysis: fields the extraction missed or got wrong, a summary and warnings (IDP-03 ch. 4.2)."""

    fields: Sequence[Mapping[str, object]]
    summary: str
    warnings: Sequence[str]
    model_id: str
    prompt_version: str


class AiAnalysisPort(Protocol):
    """ai-gateway's ``page-analysis`` (F-EX-09)."""

    def analyse(
        self, pages: Sequence[ObjectRef], current: Mapping[str, object], question: str | None, context: AiCallContext
    ) -> AiAnalysis:
        """Raises ``AiUnavailableError`` when it can't answer."""
        ...


# --- Template designer work (samples and test runs, IDP-03 ch. 2.9) --------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SampleWork:
    id: UUID
    template_id: UUID
    version: int
    account_id: UUID | None  # None: a standard template
    files: tuple[ObjectRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TestSample:
    __test__ = False

    sample_id: str
    files: tuple[ObjectRef, ...]
    expected: Mapping[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class TestRunWork:
    __test__ = False

    id: UUID
    template_id: UUID
    version: int
    account_id: UUID | None
    definition: Mapping[str, object]  # schema v1 JSON of the tested version
    samples: Sequence[TestSample]


class DesignerWorkStorePort(Protocol):
    """The designer's rows, as the workers change them. Each call is its own short transaction."""

    def pending_sample(self, sample_id: UUID) -> SampleWork | None:
        """The sample if it still needs preparing, else None (prepared already, or unknown)."""
        ...

    def sample_ready(self, sample_id: UUID, *, page_key: str, words_key: str) -> None: ...

    def sample_failed(self, sample_id: UUID, code: str) -> None: ...

    def start_test_run(self, run_id: UUID) -> TestRunWork | None:
        """QUEUED -> RUNNING; None when it isn't queued (a repeat, or unknown)."""
        ...

    def finish_test_run(self, run_id: UUID, report: Mapping[str, Any]) -> None:
        """COMPLETED, with the report; the version's ``accuracy`` is updated too."""
        ...

    def fail_test_run(self, run_id: UUID, code: str) -> None: ...


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


class PipelineRunRepositoryPort(Protocol):
    def add(self, run: PipelineRun) -> None: ...


class UnitOfWorkPort(Protocol):
    """One database transaction: item changes, the inbox and outbox rows commit together (OBX-01, MSG-02)."""

    @property
    def items(self) -> ExtractionItemRepositoryPort: ...

    @property
    def runs(self) -> PipelineRunRepositoryPort: ...

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
    def enqueue(self, item_id: UUID, kind: JobKind = JobKind.EXTRACTION) -> None:
        """Hand the item to a worker: the ``pipeline`` queue, or ``ai`` for deep analysis (IDP-03 ch. 2.2)."""
        ...


class PipelineMetricsPort(Protocol):
    """IDP-03 ch. 2.12's metrics, from each finished or failed item."""

    def run_finished(self, run: PipelineRun) -> None: ...

    def item_failed(self, code: str) -> None: ...
