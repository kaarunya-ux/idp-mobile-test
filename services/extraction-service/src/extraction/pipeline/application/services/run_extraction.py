"""Use case: run one job item through the pipeline (IDP-03 ch. 2.6), in a Celery worker.

SCAN, QUALITY_CHECK and PREPARE (``PrepareDocumentService``), then CLASSIFY, READ, VERIFY, AI_ASSIST and ASSEMBLE
(``ExtractDocumentService``). Every stage's progress is committed with a ``JobProgressed`` outbox row. The prepared
pages and ``result.json`` go to the derived bucket under ``<accountId>/<documentId>/<itemId>/``, then
``ExtractionCompleted`` names the result.

Business errors (an unreadable file, a blurred photo) end the item with their code. Anything else is a dependency
or a bug: the item goes back to ``QUEUED`` and the exception goes up, so the task retries it (CEL-04) or, after the
last retry, calls ``fail``.

Pages are read from the first file. A second file (a card's back) is prepared and kept with the result, but reading
fields across both sides comes with two-sided templates (build step E3).
"""

import json
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
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.domain.jobs import (
    ExtractionEvent,
    ExtractionItem,
    ExtractionOutcome,
    ExtractionRequest,
    ObjectRef,
    ResultSummary,
)
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import (
    AiCallContext,
    DigitalTextReaderPort,
    ObjectStoragePort,
    PageImageStorePort,
    ProgressReporterPort,
    TemplateCatalogPort,
    UnitOfWorkFactory,
)
from extraction.pipeline.domain.stages import Stage
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
    def __init__(
        self,
        units: UnitOfWorkFactory,
        storage: ObjectStoragePort,
        pages: PageImageStorePort,
        pipeline: PipelineFactory,
        clock: Clock,
        *,
        derived_bucket: str,
        stale_after: timedelta,
    ) -> None:
        self._units = units
        self._storage = storage
        self._pages = pages
        self._pipeline = pipeline
        self._clock = clock
        self._derived_bucket = derived_bucket
        self._stale_after = stale_after

    def run(self, command: RunExtractionCommand) -> None:
        item = self._start(command.item_id)
        if item is None:
            return
        try:
            outcome = self._extract(item.request)
        except DomainError as error:
            self.fail(item.id, error)
            return
        except Exception:
            self._transition(item.id, lambda it: it.requeue(self._clock.now()))  # let the retry start it again
            raise
        self._transition(item.id, lambda it: it.complete(outcome, self._clock.now()))
        log.info("extraction_completed", item_id=str(item.id), fields=outcome.summary.fields, ai_calls=outcome.ai_calls)

    def fail(self, item_id: UUID, error: DomainError) -> None:
        """End the item with ``error`` (also called by the task after its last retry, or on a time limit)."""
        self._transition(
            item_id,
            lambda it: it.fail(error.code, retryable=error.retryable, detail=error.detail, now=self._clock.now()),
        )
        log.warning("extraction_failed", item_id=str(item_id), code=error.code, retryable=error.retryable)

    def _start(self, item_id: UUID) -> ExtractionItem | None:
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None or not item.can_start(self._clock.now(), stale_after=self._stale_after):
                return None  # running elsewhere, finished, or a task for an item that was never stored
            unit.add_event(item.start(self._clock.now()))
            unit.items.save(item)
            unit.commit()
        return item

    def _extract(self, request: ExtractionRequest) -> ExtractionOutcome:
        progress = _ItemProgress(self, request.context.item_id)
        prepare = self._pipeline.prepare_service(progress)
        contents = [self._storage.read_bytes(f) for f in request.files]
        prepared_files = [prepare.prepare(PrepareDocumentCommand(content=content)) for content in contents]
        digital = self._pipeline.digital_reader.read_first_page(contents[0])
        pages = tuple(page for result in prepared_files for page in result.pages)
        qualities = tuple(quality for result in prepared_files for quality in result.qualities)
        pages = tuple(_renumbered(pages))
        templates = self._pipeline.catalog.templates_for(request.context.tenant_id, request.template_id)
        stored = self._store_pages(request, pages)  # before the AI step: ai-gateway reads the pages by key
        context = AiCallContext(
            account_id=request.context.tenant_id, priority=request.priority, page_refs=tuple(stored)
        )
        result = self._pipeline.extract_service(progress, templates).extract(
            ExtractDocumentCommand(pages=pages, qualities=qualities, digital_page=digital, ai_context=context)
        )
        _add_image_keys(result, pages, stored)
        result_ref = ObjectRef(bucket=self._derived_bucket, key=request.derived_key(RESULT_NAME))
        data = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        self._storage.write_bytes(result_ref, data, RESULT_CONTENT_TYPE)
        return _outcome(result, result_ref, len(pages))

    def _store_pages(self, request: ExtractionRequest, pages: Sequence[PreparedPage]) -> list[ObjectRef]:
        """Each prepared page as page-N.png in the derived bucket."""
        stored = []
        for page in pages:
            ref = ObjectRef(bucket=self._derived_bucket, key=request.derived_key(f"page-{page.page_index}.png"))
            self._pages.save_page(ref, page.clean)
            stored.append(ref)
        return stored

    def progress(self, item_id: UUID, stage: Stage, percent: int) -> None:
        self._transition(item_id, lambda it: it.progress(stage, percent, self._clock.now()))

    def _transition(self, item_id: UUID, change: Callable[[ExtractionItem], ExtractionEvent | None]) -> None:
        """Lock the item, apply ``change``, and commit it with the event it returns."""
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None:
                return
            event = change(item)
            unit.items.save(item)
            if event is not None:
                unit.add_event(event)
            unit.commit()


class _ItemProgress:
    """``ProgressReporterPort`` for one item: every report becomes a ``JobProgressed`` outbox row."""

    def __init__(self, runner: RunExtractionService, item_id: UUID) -> None:
        self._runner = runner
        self._item_id = item_id

    def report(self, stage: Stage, percent: int) -> None:
        self._runner.progress(self._item_id, stage, percent)


def _renumbered(pages: Sequence[PreparedPage]) -> Sequence[PreparedPage]:
    """Pages of a second file continue the numbering of the first, so keys and indexes never collide."""
    return [page if page.page_index == index else replace(page, page_index=index) for index, page in enumerate(pages)]


def _outcome(result: dict[str, Any], result_ref: ObjectRef, pages: int) -> ExtractionOutcome:
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
