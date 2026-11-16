"""Use case: record an incoming ``ExtractionRequested`` and queue the work (IDP-03 ch. 2.2, process ``intake``).

Runs in the consumer and never reads a document itself. In one transaction it decides what the request means for
the item (see ``ExtractionItem``); the task is queued after the commit. If the process dies in between, the
unacknowledged message comes back with the same ``eventId``, the inbox says "seen", the item is still ``QUEUED``,
and the task is queued again.
"""

from datetime import timedelta

import structlog

from extraction.core.clock import Clock
from extraction.pipeline.application.commands import AcceptExtractionCommand, RejectExtractionCommand
from extraction.pipeline.domain.jobs import ExtractionItem, ExtractionRequest, IntakeAction, ItemStatus, WorkFailed
from extraction.pipeline.domain.ports import TaskQueuePort, UnitOfWorkFactory, UnitOfWorkPort

log = structlog.get_logger(__name__)


class AcceptExtractionService:
    def __init__(self, units: UnitOfWorkFactory, tasks: TaskQueuePort, clock: Clock, *, stale_after: timedelta) -> None:
        self._units = units
        self._tasks = tasks
        self._clock = clock
        self._stale_after = stale_after

    def accept(self, command: AcceptExtractionCommand) -> IntakeAction:
        item_id = command.request.context.item_id
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if unit.record_message_once(command.message_id, command.message_type):
                item, action = self._apply(unit, item, command.request)
            else:  # a redelivery: only repair a task that may have been lost
                action = IntakeAction.ENQUEUE if item and item.status is ItemStatus.QUEUED else IntakeAction.SKIP
            unit.commit()
        if action is IntakeAction.ENQUEUE:
            self._tasks.enqueue(item_id, command.request.kind)
        log.info("extraction_request_accepted", item_id=str(item_id), action=action)
        return action

    def reject(self, command: RejectExtractionCommand) -> None:
        """Tell Java the item failed when the request itself can't be used."""
        error = command.error
        with self._units() as unit:
            if unit.record_message_once(command.message_id, command.message_type):
                unit.add_event(
                    WorkFailed(
                        item=command.item, code=error.code, retryable=error.retryable, detail=error.detail, sequence=1
                    )
                )
            unit.commit()
        log.warning("extraction_request_rejected", item_id=str(command.item.item_id), code=error.code)

    def _apply(
        self, unit: UnitOfWorkPort, item: ExtractionItem | None, request: ExtractionRequest
    ) -> tuple[ExtractionItem, IntakeAction]:
        now = self._clock.now()
        if item is None:
            item = ExtractionItem.receive(request, now)
            unit.items.add(item)
            return item, IntakeAction.ENQUEUE
        action = item.on_new_request(request, now, stale_after=self._stale_after)
        unit.items.save(item)
        if action is IntakeAction.RESEND_COMPLETION:
            unit.add_event(item.stored_completion())
        return item, action
