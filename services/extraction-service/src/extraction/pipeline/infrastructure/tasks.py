"""Celery: the app, the one task, and the queue the consumer sends to (standard ch. 16.2; IDP-03 ch. 2.2).

The task is thin (CEL-01): it takes an item id (CEL-02) and calls ``RunExtractionService``, which checks the item
before doing anything, so a repeat is harmless (CEL-03). Passing failures (database, storage) retry with backoff
and jitter, a bounded number of times (CEL-04); after that, and for anything unexpected, the item fails with
``EXT-500-INTERNAL-001`` (retryable, so Java may dispatch it again).

IDP-03 ch. 2.6 describes a Celery canvas with a pool per OCR engine; here one task runs the whole pipeline and the
engines read in threads of the same process (ADR 0003, item 10). Splitting into a canvas is a scaling change, not a
behaviour change.
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

from extraction.core.config import CelerySettings
from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.exceptions import ExtractionTimeoutError, UnexpectedFailureError

log = structlog.get_logger(__name__)

TASK_NAME = "extraction.run_item"
PIPELINE_QUEUE = "pipeline"
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

    provider: Callable[[], RunExtractionService] | None = None
    soft_time_limit_seconds: float = 0.0

    def service(self) -> RunExtractionService:
        if self.provider is None:
            raise RuntimeError("the worker started without bind_service(); see extraction.worker")
        return self.provider()


_binding = _Binding()


def bind_service(provider: Callable[[], RunExtractionService], settings: CelerySettings) -> None:
    _binding.provider = provider
    _binding.soft_time_limit_seconds = settings.soft_time_limit.total_seconds()


def create_celery_app(settings: CelerySettings) -> Celery:
    """The Celery app, configured as standard ch. 16.2 shows. The consumer uses it only to send tasks."""
    app = Celery("extraction")
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
        task_default_queue=PIPELINE_QUEUE,
        # A topic exchange: Celery's delayed retries route only to topic exchanges (see conversion-service)
        task_queues=[
            Queue(
                PIPELINE_QUEUE,
                Exchange(PIPELINE_QUEUE, type="topic"),
                routing_key=PIPELINE_QUEUE,
                queue_arguments={"x-queue-type": "quorum"},
            )
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

    def enqueue(self, item_id: UUID) -> None:
        self._app.send_task(TASK_NAME, args=[str(item_id)], queue=PIPELINE_QUEUE)


if TYPE_CHECKING:  # the celery-types stubs make Task generic; the real class isn't subscriptable
    _TaskBase = Task[[str], None]
else:
    _TaskBase = Task


class _ExtractionTask(_TaskBase):
    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        """The last retry failed, or something unexpected happened: end the item so Java isn't left waiting."""
        item_id = UUID(str(args[0]))
        log.error("extraction_task_failed", item_id=str(item_id), error=repr(exc), exc_info=exc)
        _binding.service().fail(item_id, UnexpectedFailureError())


@shared_task(
    name=TASK_NAME,
    bind=True,
    base=_ExtractionTask,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,  # overridden by CELERY__MAX_RETRIES through task_annotations
)
def run_item(self: _ExtractionTask, item_id: str) -> None:
    service = _binding.service()
    try:
        service.run(RunExtractionCommand(item_id=UUID(item_id)))
    except SoftTimeLimitExceeded:  # CEL-06: the soft limit lets the item end cleanly before the hard kill
        service.fail(UUID(item_id), ExtractionTimeoutError(_binding.soft_time_limit_seconds))
