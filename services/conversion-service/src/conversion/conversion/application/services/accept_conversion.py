"""Use case: record an incoming ``ConversionRequested`` and queue the work (IDP-03 ch. 3.2, process ``intake``).

Runs in the consumer. It never converts anything; it decides, in one transaction, what the request means for
the item (see ``ConversionItem``), then hands the item to a worker pool. The task is queued after the commit:
if the process dies in between, the unacknowledged message comes back with the same ``eventId``, the inbox
says "seen", and the item is still ``QUEUED``, so the task is queued again.
"""

from datetime import timedelta

import structlog

from conversion.conversion.application.commands import AcceptConversionCommand, RejectConversionCommand
from conversion.conversion.domain.events import WorkFailed
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, IntakeAction, ItemStatus
from conversion.conversion.domain.options import parse_job_options
from conversion.conversion.domain.policies import worker_pool_for
from conversion.conversion.domain.ports import TaskQueuePort, UnitOfWorkFactory, UnitOfWorkPort
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
from conversion.conversion.domain.values import WorkerPool
from conversion.core.clock import Clock
from conversion.core.errors import DomainError

log = structlog.get_logger(__name__)


class AcceptConversionService:
    def __init__(
        self,
        units: UnitOfWorkFactory,
        tasks: TaskQueuePort,
        planner: RoutePlanner,
        clock: Clock,
        *,
        stale_after: timedelta,
    ) -> None:
        self._units = units
        self._tasks = tasks
        self._planner = planner
        self._clock = clock
        self._stale_after = stale_after

    def accept(self, command: AcceptConversionCommand) -> IntakeAction:
        item_id = command.request.context.item_id
        pool: WorkerPool | None = None
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if unit.record_message_once(command.message_id, command.message_type):
                item, action = self._apply(unit, item, command.request)
            else:  # a redelivery: only repair a task that may have been lost
                action = IntakeAction.ENQUEUE if item and item.status is ItemStatus.QUEUED else IntakeAction.SKIP
            if action is IntakeAction.ENQUEUE and item is not None:
                pool = self._pool_or_fail(unit, item)
                action = action if pool is not None else IntakeAction.SKIP
            unit.commit()
        if pool is not None:
            self._tasks.enqueue(item_id, pool)
        log.info("conversion_request_accepted", item_id=str(item_id), action=action, pool=pool)
        return action

    def reject(self, command: RejectConversionCommand) -> None:
        """Tell Java the item failed when the request itself can't be used (an unknown input type, say)."""
        error = command.error
        with self._units() as unit:
            if unit.record_message_once(command.message_id, command.message_type):
                unit.add_event(
                    WorkFailed(
                        item=command.item, code=error.code, retryable=error.retryable, detail=error.detail, sequence=1
                    )
                )
            unit.commit()
        log.warning("conversion_request_rejected", item_id=str(command.item.item_id), code=error.code)

    def _apply(
        self, unit: UnitOfWorkPort, item: ConversionItem | None, request: ConversionRequest
    ) -> tuple[ConversionItem, IntakeAction]:
        now = self._clock.now()
        if item is None:
            item = ConversionItem.receive(request, now)
            unit.items.add(item)
            return item, IntakeAction.ENQUEUE
        action = item.on_new_request(request, now, stale_after=self._stale_after)
        unit.items.save(item)
        if action is IntakeAction.RESEND_COMPLETION:
            unit.add_event(item.stored_completion())
        return item, action

    def _pool_or_fail(self, unit: UnitOfWorkPort, item: ConversionItem) -> WorkerPool | None:
        """The pool to queue on; or, when there is no route or the options are wrong, fail the item now."""
        try:
            return self._pool_for(item.request)
        except DomainError as error:
            unit.add_event(item.fail(error.code, retryable=error.retryable, detail=error.detail, now=self._clock.now()))
            unit.items.save(item)
            return None

    def _pool_for(self, request: ConversionRequest) -> WorkerPool:
        """Routes are planned as if a PDF were digital; a worker that finds a scan re-plans (OCR is not here yet)."""
        options = parse_job_options(request.options)
        route = self._planner.get_route(RouteRequest(source=request.source, target=request.target, ocr=options.ocr))
        return worker_pool_for(route)
