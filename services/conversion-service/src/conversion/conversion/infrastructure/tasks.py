"""Celery: the app, the one task, and the queue the consumer sends to (standard ch. 16.2; IDP-03 ch. 3.2).

The task is thin (CEL-01): it takes an item id (CEL-02) and calls ``ProcessConversionService``, which checks the
item before doing anything, so a repeat is harmless (CEL-03). Passing failures (database, storage) retry with
backoff and jitter, a bounded number of times (CEL-04); after that, and for anything unexpected, the item fails
with ``CNV-500-INTERNAL-001`` (retryable, so Java may dispatch it again). One queue per worker pool (CEL-05).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from celery import Celery, Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from kombu import Exchange, Queue
from sqlalchemy.exc import InterfaceError, OperationalError

from conversion.conversion.application.commands import ProcessItemCommand
from conversion.conversion.application.services.process_conversion import ProcessConversionService
from conversion.conversion.domain.exceptions import ConversionTimeoutError, UnexpectedFailureError
from conversion.conversion.domain.values import WorkerPool
from conversion.core.config import CelerySettings

log = structlog.get_logger(__name__)

TASK_NAME = "conversion.convert_item"
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,  # database unreachable or a timeout
    InterfaceError,
    BotoCoreError,  # storage unreachable
    ClientError,  # storage refused (throttling, a 5xx)
    ConnectionError,
    TimeoutError,
)


@dataclass(slots=True)
class _Binding:
    """The worker process's service, set once in ``worker_process_init`` (CEL-07)."""

    provider: Callable[[], ProcessConversionService] | None = None
    soft_time_limit_seconds: float = 0.0

    def service(self) -> ProcessConversionService:
        if self.provider is None:
            raise RuntimeError("the worker started without bind_service(); see conversion.worker")
        return self.provider()


_binding = _Binding()


def bind_service(provider: Callable[[], ProcessConversionService], settings: CelerySettings) -> None:
    _binding.provider = provider
    _binding.soft_time_limit_seconds = settings.soft_time_limit.total_seconds()


def create_celery_app(settings: CelerySettings) -> Celery:
    """The Celery app, configured as standard ch. 16.2 shows. The consumer uses it only to send tasks."""
    app = Celery("conversion")
    app.conf.update(
        broker_url=settings.broker_url.get_secret_value(),
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_acks_late=True,  # acknowledged after the task finishes
        task_reject_on_worker_lost=True,  # a crashed worker's task is delivered again
        worker_prefetch_multiplier=1,  # long tasks: no hoarding
        task_time_limit=int(settings.time_limit.total_seconds()),
        task_soft_time_limit=int(settings.soft_time_limit.total_seconds()),
        task_default_queue=WorkerPool.DATA.value,
        # Topic exchanges: quorum queues have no per-message delay, so Celery delays a retry through its own
        # delayed-delivery exchanges, which only route to topic exchanges. A direct one would hold the worker.
        task_queues=[
            Queue(
                pool.value,
                Exchange(pool.value, type="topic"),
                routing_key=pool.value,
                queue_arguments={"x-queue-type": "quorum"},
            )
            for pool in WorkerPool
        ],
        task_default_queue_type="quorum",
        worker_detect_quorum_queues=True,
        broker_transport_options={"confirm_publish": True},
        broker_connection_retry_on_startup=True,
        task_ignore_result=True,
        result_backend=None,  # the database is the truth, not Celery results
        task_annotations={TASK_NAME: {"max_retries": settings.max_retries}},
        worker_hijack_root_logger=False,  # keep the structlog setup
    )
    return app


class CeleryTaskQueue:
    """``TaskQueuePort``: sends the task by name, so the consumer never imports worker code."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    def enqueue(self, item_id: UUID, pool: WorkerPool) -> None:
        self._app.send_task(TASK_NAME, args=[str(item_id)], queue=pool.value)


if TYPE_CHECKING:  # the celery-types stubs make Task generic; the real class isn't subscriptable
    _TaskBase = Task[[str], None]
else:
    _TaskBase = Task


class _ConversionTask(_TaskBase):
    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        """The last retry failed, or something unexpected happened: end the item so Java isn't left waiting."""
        item_id = UUID(str(args[0]))
        log.error("conversion_task_failed", item_id=str(item_id), error=repr(exc), exc_info=exc)
        _binding.service().fail(item_id, UnexpectedFailureError())


@shared_task(
    name=TASK_NAME,
    bind=True,
    base=_ConversionTask,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,  # overridden by CELERY__MAX_RETRIES through task_annotations
)
def convert_item(self: _ConversionTask, item_id: str) -> None:
    service = _binding.service()
    try:
        service.process(ProcessItemCommand(item_id=UUID(item_id)))
    except SoftTimeLimitExceeded:  # CEL-06: the soft limit lets the item end cleanly before the hard kill
        service.fail(UUID(item_id), ConversionTimeoutError(_binding.soft_time_limit_seconds))
