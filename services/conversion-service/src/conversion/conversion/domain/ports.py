"""What the conversion rules need from outside, described in domain terms (standard ch. 7, ports).

Infrastructure adapters implement these protocols; the application layer depends only on the protocols, so it
never imports Pillow, PDFium or LibreOffice code directly.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from conversion.conversion.domain.edit_plan import EditPlan
from conversion.conversion.domain.events import ConversionEvent
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.results import ExportOptions, ResultDocument
from conversion.conversion.domain.route import Converter, Step
from conversion.conversion.domain.values import ObjectRef, ThumbnailSpec, WorkerPool


@dataclass(frozen=True, slots=True, kw_only=True)
class StepRequest:
    """One step to run on files in a local working folder."""

    step: Step
    inputs: tuple[Path, ...]  # usually one file; several only when images become one PDF or deck
    output_dir: Path
    options: ConversionOptions


@dataclass(frozen=True, slots=True, kw_only=True)
class StepResult:
    """What a step wrote, and how many pages or images it processed."""

    outputs: tuple[Path, ...]
    page_count: int


class ConverterPort(Protocol):
    """Runs one kind of conversion step."""

    @property
    def converter(self) -> Converter:
        """Which step this implementation runs."""
        ...

    def convert(self, request: StepRequest) -> StepResult:
        """Write the step's output into ``request.output_dir``. Raise a ``DomainError`` when the input is bad."""
        ...


class ConverterLookupPort(Protocol):
    """Finds the implementation for a kind of step."""

    def get(self, converter: Converter) -> ConverterPort:
        """Return the implementation of ``converter``; raise ``LookupError`` if this process has none."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadedObject:
    size: int
    sha256: str  # hex


class ObjectStoragePort(Protocol):
    """MinIO through the S3 API. Files stream to and from local disk, never whole into memory (FIL-07)."""

    def download(self, ref: ObjectRef, destination: Path) -> None:
        """Write the object to ``destination``. Raise ``LimitExceededError`` if it is bigger than allowed."""
        ...

    def upload(self, source: Path, ref: ObjectRef, content_type: str) -> UploadedObject:
        """Store ``source`` at ``ref`` and return its size and SHA-256."""
        ...


class FileInspectorPort(Protocol):
    def is_scanned_pdf(self, path: Path) -> bool:
        """``True`` when the PDF has no text layer, so its text can only be read with OCR (IDP-03 ch. 3.4)."""
        ...

    def count_pages(self, path: Path, file_format: Format) -> int | None:
        """Pages of a PDF, slides of a deck, 1 for an image; ``None`` for formats without fixed pages."""
        ...


class PdfMergerPort(Protocol):
    def merge(self, pdfs: Sequence[Path], output: Path) -> int:
        """Write every page of ``pdfs``, in order, to ``output`` and return the page count.

        Raise ``PasswordRequiredError`` for an encrypted input, ``CorruptPdfError`` for a damaged one, and
        ``LimitExceededError`` when the result would have more pages than allowed.
        """
        ...


class ImageThumbnailPort(Protocol):
    def thumbnail(self, source: Path, output: Path, spec: ThumbnailSpec) -> None:
        """Write ``source`` (any supported image) upright, shrunk to fit ``spec``, as a JPEG at ``output``."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class EditJob:
    """An edit plan ready to apply: secrets decrypted, every document and image downloaded."""

    plan: EditPlan
    documents: Mapping[str, Path]  # documentId -> PDF; "self" is the job's own input
    images: Mapping[str, Path]  # imageKey -> image file


class PdfEditorPort(Protocol):
    def edit(self, job: EditJob, output_dir: Path) -> tuple[tuple[Path, ...], int]:
        """Apply the plan; return the PDF (or several, when the plan splits it) and the page count. Raise
        ``InvalidPlanError`` (``CNV-422-PLAN-001``) when the plan doesn't fit the document, and the usual PDF
        errors."""
        ...


class SecretsPort(Protocol):
    def decrypt(self, token: str) -> str:
        """A password Java encrypted with this service's public key (IDP-03 ch. 3.7). Never logged."""
        ...


class ResultExporterPort(Protocol):
    def export(
        self,
        results: Sequence[ResultDocument],
        target: Format,
        options: ExportOptions,
        output_dir: Path,
        thumbnails: Mapping[str, Path],
    ) -> tuple[Path, ...]:
        """Write the results as JSON, CSV, XLSX, DOCX or XML (PDF is the DOCX report, converted by the caller)."""
        ...


class ConversionItemRepositoryPort(Protocol):
    def get_for_update(self, item_id: UUID) -> ConversionItem | None:
        """The item, locked until the unit of work ends, so two workers can't change it at once."""
        ...

    def add(self, item: ConversionItem) -> None: ...

    def save(self, item: ConversionItem) -> None: ...


class UnitOfWorkPort(Protocol):
    """One database transaction: the item's state, the inbox row and the outbox rows commit together (OBX-01)."""

    @property
    def items(self) -> ConversionItemRepositoryPort: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Roll back unless ``commit`` was called."""
        ...

    def record_message_once(self, message_id: UUID, message_type: str) -> bool:
        """``False`` when this message was handled before (MSG-02)."""
        ...

    def add_event(self, event: ConversionEvent) -> None:
        """Queue ``event`` in the outbox, in this transaction."""
        ...

    def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


class TaskQueuePort(Protocol):
    def enqueue(self, item_id: UUID, pool: WorkerPool) -> None:
        """Ask a worker in ``pool`` to process the item. Workers check the item first, so a repeat is harmless."""
        ...
