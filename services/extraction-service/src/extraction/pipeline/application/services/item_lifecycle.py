"""The job item's lifecycle in the worker, shared by extraction, recheck and deep analysis.

Every change locks the item, applies one transition and commits it with the event it returns (``JobProgressed``,
``ExtractionCompleted`` or ``WorkFailed``), so the outbox and the item never disagree (OBX-01). Completion also writes
the ``pipeline_runs`` row in the same transaction.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import structlog

from extraction.core.clock import Clock
from extraction.core.errors import DomainError
from extraction.pipeline.domain.jobs import ExtractionEvent, ExtractionItem, ExtractionOutcome
from extraction.pipeline.domain.ports import PipelineMetricsPort, UnitOfWorkFactory
from extraction.pipeline.domain.runs import PipelineRun
from extraction.pipeline.domain.stages import Stage

log = structlog.get_logger(__name__)


class ItemLifecycle:
    def __init__(
        self,
        units: UnitOfWorkFactory,
        clock: Clock,
        *,
        stale_after: timedelta,
        metrics: PipelineMetricsPort | None = None,
    ) -> None:
        self._units = units
        self._clock = clock
        self._stale_after = stale_after
        self._metrics = metrics

    @property
    def clock(self) -> Clock:
        return self._clock

    def start(self, item_id: UUID) -> ExtractionItem | None:
        """The item, now RUNNING; None when it is running elsewhere, finished, or was never stored."""
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None or not item.can_start(self._clock.now(), stale_after=self._stale_after):
                return None
            unit.add_event(item.start(self._clock.now()))
            unit.items.save(item)
            unit.commit()
        return item

    def progress(self, item_id: UUID, stage: Stage, percent: int) -> None:
        self._transition(item_id, lambda it: it.progress(stage, percent, self._clock.now()))

    def complete(self, item_id: UUID, outcome: ExtractionOutcome, run: PipelineRun) -> None:
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None:
                return
            unit.add_event(item.complete(outcome, self._clock.now()))
            unit.items.save(item)
            unit.runs.add(run)
            unit.commit()
        if self._metrics is not None:
            self._metrics.run_finished(run)
        log.info("extraction_completed", item_id=str(item_id), kind=run.kind, ai_calls=run.ai_calls)

    def requeue(self, item_id: UUID) -> None:
        """A dependency failed mid-run and the task will retry: let the retry start it again."""
        self._transition(item_id, lambda it: it.requeue(self._clock.now()))

    def fail(self, item_id: UUID, error: DomainError) -> None:
        self._transition(
            item_id,
            lambda it: it.fail(error.code, retryable=error.retryable, detail=error.detail, now=self._clock.now()),
        )
        if self._metrics is not None:
            self._metrics.item_failed(error.code)
        log.warning("extraction_failed", item_id=str(item_id), code=error.code, retryable=error.retryable)

    def _transition(self, item_id: UUID, change: Callable[[ExtractionItem], ExtractionEvent | None]) -> None:
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None:
                return
            event = change(item)
            unit.items.save(item)
            if event is not None:
                unit.add_event(event)
            unit.commit()


class StageClock:
    """``ProgressReporterPort`` for one item: every report becomes a ``JobProgressed`` row, and the first report of
    each stage is timed for ``pipeline_runs``."""

    def __init__(
        self, lifecycle: ItemLifecycle, item_id: UUID, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._lifecycle = lifecycle
        self._item_id = item_id
        self._monotonic = monotonic
        self.started = monotonic()
        self.starts: list[tuple[str, float]] = []

    def report(self, stage: Stage, percent: int) -> None:
        if stage.value not in {name for name, _ in self.starts}:
            self.starts.append((stage.value, self._monotonic()))
        self._lifecycle.progress(self._item_id, stage, percent)

    def elapsed_ms(self) -> int:
        return round((self._monotonic() - self.started) * 1000)

    def now(self) -> float:
        return self._monotonic()
