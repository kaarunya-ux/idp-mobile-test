"""Use case: run one job item through the pipeline (IDP-03 ch. 2.6), in a Celery worker.

SCAN, QUALITY_CHECK and PREPARE (``PrepareDocumentService``), then CLASSIFY, READ, VERIFY, AI_ASSIST and ASSEMBLE
(``ExtractDocumentService``). Every stage's progress is committed with a ``JobProgressed`` outbox row. The prepared
pages and ``result.json`` go to the derived bucket under ``<accountId>/<documentId>/<itemId>/``, then
``ExtractionCompleted`` names the result.

Business errors (an unreadable file, a blurred photo) end the item with their code. Anything else is a dependency
or a bug: the item goes back to ``QUEUED`` and the exception goes up, so the task retries it (CEL-04) or, after the
last retry, calls ``fail``.

Word, Excel, PowerPoint and OpenDocument files are turned into a PDF first (``OfficeConverterPort``), so they are read
like a digital PDF: exact text with positions, no OCR.

A RECHECK item runs the same stages on the uploaded original, then keeps the device's settled fields and takes the
server's reading for the weak ones (``merging.merge_recheck``). The pages are stored before the AI step, because
ai-gateway reads them by key.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog

from extraction.core.clock import Clock
from extraction.core.errors import DomainError
from extraction.pipeline.application.commands import (
    ExtractDocumentCommand,
    PrepareDocumentCommand,
    RunExtractionCommand,
)
from extraction.pipeline.application.services.extract_document import ExtractDocumentService
from extraction.pipeline.application.services.item_lifecycle import ItemLifecycle, StageClock
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.domain.jobs import (
    ExtractionOutcome,
    ExtractionRequest,
    ObjectRef,
    ResultSummary,
)
from extraction.pipeline.domain.merging import merge_recheck
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import (
    AiCallContext,
    DigitalTextReaderPort,
    ObjectStoragePort,
    OfficeConverterPort,
    PageImageStorePort,
    PipelineMetricsPort,
    ProgressReporterPort,
    TemplateCatalogPort,
    UnitOfWorkFactory,
)
from extraction.pipeline.domain.runs import PipelineRun, decision_counts, stage_durations
from extraction.pipeline.domain.template import TemplateDefinition

log = structlog.get_logger(__name__)

RESULT_NAME = "result.json"
RESULT_CONTENT_TYPE = "application/json"


class PipelineFactory(Protocol):
    """Builds the two pipeline services for one run, reporting progress to ``progress``. The engines and models
    behind them are loaded once per worker process (CEL-07); only the light services are built per run."""

    def prepare_service(self, progress: ProgressReporterPort) -> PrepareDocumentService: ...

    def extract_service(
        self, progress: ProgressReporterPort, templates: Sequence[TemplateDefinition]
    ) -> ExtractDocumentService: ...

    @property
    def catalog(self) -> TemplateCatalogPort: ...

    @property
    def digital_reader(self) -> DigitalTextReaderPort: ...


class RunExtractionService:
    # every collaborator is a port or a number
    def __init__(  # noqa: PLR0913
        self,
        units: UnitOfWorkFactory,
        storage: ObjectStoragePort,
        pages: PageImageStorePort,
        pipeline: PipelineFactory,
        clock: Clock,
        *,
        derived_bucket: str,
        stale_after: timedelta,
        review_confidence: float = 0.85,
        metrics: PipelineMetricsPort | None = None,
        office: OfficeConverterPort | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lifecycle = ItemLifecycle(units, clock, stale_after=stale_after, metrics=metrics)
        self._office = office
        self._storage = storage
        self._pages = pages
        self._pipeline = pipeline
        self._derived_bucket = derived_bucket
        self._review_confidence = review_confidence
        self._monotonic = monotonic

    def run(self, command: RunExtractionCommand) -> None:
        item = self._lifecycle.start(command.item_id)
        if item is None:
            return
        progress = StageClock(self._lifecycle, item.id, self._monotonic)
        try:
            outcome, result = self._extract(item.request, progress)
        except DomainError as error:
            self.fail(item.id, error)
            return
        except Exception:
            self._lifecycle.requeue(item.id)  # let the retry start it again
            raise
        self._lifecycle.complete(item.id, outcome, _run_row(item.request, outcome, result, progress, self._clock))

    def fail(self, item_id: UUID, error: DomainError) -> None:
        """End the item with ``error`` (also called by the task after its last retry, or on a time limit)."""
        self._lifecycle.fail(item_id, error)

    @property
    def _clock(self) -> Clock:
        return self._lifecycle.clock

    def _extract(self, request: ExtractionRequest, progress: StageClock) -> tuple[ExtractionOutcome, dict[str, Any]]:
        prepare = self._pipeline.prepare_service(progress)
        contents = [readable(self._storage.read_bytes(f), self._office) for f in request.files]
        prepared_files = [prepare.prepare(PrepareDocumentCommand(content=content)) for content in contents]
        digital = self._pipeline.digital_reader.read_first_page(contents[0])
        pages = tuple(_renumbered([page for result in prepared_files for page in result.pages]))
        qualities = tuple(quality for result in prepared_files for quality in result.qualities)
        templates = self._pipeline.catalog.templates_for(request.context.tenant_id, request.template_id)
        stored = self._store_pages(request, pages)  # before the AI step: ai-gateway reads the pages by key
        context = AiCallContext(
            account_id=request.context.tenant_id, priority=request.priority, page_refs=tuple(stored)
        )
        command = ExtractDocumentCommand(
            pages=pages,
            qualities=qualities,
            digital_page=digital,
            ai_context=context,
            template_forced=request.template_id is not None,
        )
        result = self._pipeline.extract_service(progress, templates).extract(command)
        _add_image_keys(result, pages, stored)
        if request.recheck is not None:
            on_device = json.loads(self._storage.read_bytes(request.recheck.result))
            result = merge_recheck(
                on_device, result, asked=request.recheck.fields, review_confidence=self._review_confidence
            )
        result_ref = ObjectRef(bucket=self._derived_bucket, key=request.derived_key(RESULT_NAME))
        self._storage.write_bytes(result_ref, encode_result(result), RESULT_CONTENT_TYPE)
        return outcome_of(result, result_ref, len(pages)), result

    def _store_pages(self, request: ExtractionRequest, pages: Sequence[PreparedPage]) -> list[ObjectRef]:
        """Each prepared page as page-N.png in the derived bucket."""
        stored = []
        for page in pages:
            ref = ObjectRef(bucket=self._derived_bucket, key=request.derived_key(f"page-{page.page_index}.png"))
            self._pages.save_page(ref, page.clean)
            stored.append(ref)
        return stored


def readable(content: bytes, office: OfficeConverterPort | None) -> bytes:
    """An Office document as a PDF (then read from its text layer, no OCR); anything else as it is."""
    converted = office.to_pdf(content) if office is not None else None
    return converted if converted is not None else content


def encode_result(result: dict[str, Any]) -> bytes:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()


def _run_row(
    request: ExtractionRequest, outcome: ExtractionOutcome, result: dict[str, Any], progress: StageClock, clock: Clock
) -> PipelineRun:
    return PipelineRun(
        item_id=request.context.item_id,
        account_id=request.context.tenant_id,
        kind=request.kind,
        document_type=outcome.document_type,
        template_id=outcome.template_id,
        template_version=outcome.template_version,
        stages=stage_durations(progress.starts, progress.now()),
        decisions=decision_counts(result),
        ai_calls=outcome.ai_calls,
        duration_ms=progress.elapsed_ms(),
        created_at=clock.now(),
    )


def _renumbered(pages: Sequence[PreparedPage]) -> Sequence[PreparedPage]:
    """Pages of a second file continue the numbering of the first, so keys and indexes never collide."""
    return [page if page.page_index == index else replace(page, page_index=index) for index, page in enumerate(pages)]


def outcome_of(result: dict[str, Any], result_ref: ObjectRef, pages: int) -> ExtractionOutcome:
    fields = result.get("fields", [])
    confidences = [float(f["confidence"]) for f in fields if isinstance(f.get("confidence"), int | float)]
    template = result.get("template") or {}
    validation = result.get("validation", {})
    return ExtractionOutcome(
        result=result_ref,
        document_type=(result.get("documentType") or {}).get("code"),
        template_id=template.get("id"),
        template_version=template.get("version"),
        summary=ResultSummary(
            fields=len(fields),
            needs_review=int(validation.get("needsReview", 0)),
            avg_confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        ),
        pages=pages,
        ai_calls=len((result.get("provenance") or {}).get("models", [])),
    )


def _add_image_keys(result: dict[str, Any], pages: Sequence[PreparedPage], stored: Sequence[ObjectRef]) -> None:
    """Each page's image key in the result (``pages[].imageKey``); pages the reader didn't summarise (a card's back)
    are added."""
    for summary, ref in zip(result["pages"], stored, strict=False):
        summary["imageKey"] = ref.key
    for page, ref in list(zip(pages, stored, strict=True))[len(result["pages"]) :]:
        result["pages"].append(
            {"index": page.page_index, "width": page.clean.width, "height": page.clean.height, "imageKey": ref.key}
        )
