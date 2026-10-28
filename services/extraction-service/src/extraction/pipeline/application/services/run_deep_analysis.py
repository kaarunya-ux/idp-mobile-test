"""Use case: deep analysis (IDP-03 ch. 2.6, F-EX-09), in a Celery worker on the ``ai`` queue.

The stored result and its page images go to ai-gateway's ``page-analysis``; what comes back is merged under
``result.analysis`` without touching accepted fields (``merging.merge_analysis``), and written as this item's
``result.json``. Only field keys, labels, values and statuses go to the gateway: no readings or boxes.
"""

import dataclasses
import json
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from extraction.core.clock import Clock
from extraction.core.errors import DomainError
from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.application.services.item_lifecycle import ItemLifecycle, StageClock
from extraction.pipeline.application.services.run_extraction import (
    RESULT_CONTENT_TYPE,
    RESULT_NAME,
    encode_result,
    outcome_of,
)
from extraction.pipeline.domain.exceptions import AiAnalysisUnavailableError, AiUnavailableError, RequestRejectedError
from extraction.pipeline.domain.jobs import ExtractionOutcome, ExtractionRequest, ObjectRef
from extraction.pipeline.domain.merging import merge_analysis
from extraction.pipeline.domain.ports import (
    AiAnalysisPort,
    AiCallContext,
    ObjectStoragePort,
    PipelineMetricsPort,
    UnitOfWorkFactory,
)
from extraction.pipeline.domain.runs import PipelineRun, decision_counts, stage_durations
from extraction.pipeline.domain.stages import Stage

_DONE = 100
_SENT_FIELD_KEYS = ("key", "label", "value", "status")


class RunDeepAnalysisService:
    # every collaborator is a port or a number
    def __init__(  # noqa: PLR0913
        self,
        units: UnitOfWorkFactory,
        storage: ObjectStoragePort,
        analyser: AiAnalysisPort | None,
        clock: Clock,
        *,
        derived_bucket: str,
        stale_after: timedelta,
        metrics: PipelineMetricsPort | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lifecycle = ItemLifecycle(units, clock, stale_after=stale_after, metrics=metrics)
        self._storage = storage
        self._analyser = analyser
        self._clock = clock
        self._derived_bucket = derived_bucket
        self._monotonic = monotonic

    def run(self, command: RunExtractionCommand) -> None:
        item = self._lifecycle.start(command.item_id)
        if item is None:
            return
        progress = StageClock(self._lifecycle, item.id, self._monotonic)
        try:
            outcome, result = self._analyse(item.request, progress)
        except DomainError as error:
            self.fail(item.id, error)
            return
        except Exception:
            self._lifecycle.requeue(item.id)
            raise
        run = PipelineRun(
            item_id=item.id,
            account_id=item.request.context.tenant_id,
            kind=item.request.kind,
            document_type=outcome.document_type,
            template_id=outcome.template_id,
            template_version=outcome.template_version,
            stages=stage_durations(progress.starts, progress.now()),
            decisions=decision_counts(result),
            ai_calls=outcome.ai_calls,
            duration_ms=progress.elapsed_ms(),
            created_at=self._clock.now(),
        )
        self._lifecycle.complete(item.id, outcome, run)

    def fail(self, item_id: UUID, error: DomainError) -> None:
        self._lifecycle.fail(item_id, error)

    def _analyse(self, request: ExtractionRequest, progress: StageClock) -> tuple[ExtractionOutcome, dict[str, Any]]:
        spec = request.analysis
        if spec is None:
            raise RequestRejectedError("A deep analysis item without its result.")
        if self._analyser is None:
            raise AiAnalysisUnavailableError
        current = json.loads(self._storage.read_bytes(spec.result))
        progress.report(Stage.AI_ASSIST, 0)
        context = AiCallContext(account_id=request.context.tenant_id, priority=request.priority, page_refs=spec.pages)
        try:
            analysis = self._analyser.analyse(spec.pages, _summary(current), spec.question, context)
        except AiUnavailableError as exc:
            raise AiAnalysisUnavailableError from exc
        progress.report(Stage.AI_ASSIST, _DONE)
        merged = merge_analysis(
            current,
            {"fields": analysis.fields, "summary": analysis.summary, "warnings": analysis.warnings},
            model=analysis.model_id,
            prompt_version=analysis.prompt_version,
            question=spec.question,
        )
        ref = ObjectRef(bucket=self._derived_bucket, key=request.derived_key(RESULT_NAME))
        self._storage.write_bytes(ref, encode_result(merged), RESULT_CONTENT_TYPE)
        progress.report(Stage.ASSEMBLE, _DONE)
        outcome = outcome_of(merged, ref, pages=0)
        return dataclasses.replace(outcome, ai_calls=1), merged  # one call here; earlier ones were billed already


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    """What the model needs to know about the current result: the fields' keys, labels, values and statuses."""
    fields = [{key: f.get(key) for key in _SENT_FIELD_KEYS} for f in result.get("fields", [])]
    return {"documentType": (result.get("documentType") or {}).get("code"), "fields": fields}
