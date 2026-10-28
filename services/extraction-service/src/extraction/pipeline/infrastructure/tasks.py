"""Celery: the app, the tasks, and the queues the consumer and the API send to (standard ch. 16.2; IDP-03 ch. 2.2).

Queues: ``pipeline`` (extraction and recheck items, template samples and test runs: CPU work) and ``ai`` (deep
analysis: waits on ai-gateway, so a separate pool can run many at once).

Each task is thin (CEL-01): it takes an id (CEL-02) and calls one service, which checks the item before doing
anything, so a repeat is harmless (CEL-03). Passing failures (database, storage) retry with backoff
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
from extraction.pipeline.application.services.designer_work import DesignerWorkService
from extraction.pipeline.application.services.run_deep_analysis import RunDeepAnalysisService
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.exceptions import ExtractionTimeoutError, UnexpectedFailureError
from extraction.pipeline.domain.jobs import JobKind
from extraction.pipeline.domain.ports import DesignerWorkStorePort
from extraction.template.infrastructure.designer_adapters import PREPARE_SAMPLE_TASK, TEST_RUN_TASK

log = structlog.get_logger(__name__)

TASK_NAME = "extraction.run_item"
DEEP_ANALYSIS_TASK = "extraction.run_deep_analysis"
PIPELINE_QUEUE = "pipeline"
AI_QUEUE = "ai"
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,  # database unreachable or a timeout
    InterfaceError,
    BotoCoreError,  # storage unreachable
    ClientError,  # storage refused (throttling, a 5xx)
    ConnectionError,
    TimeoutError,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerServices:
    """What the tasks call, built once per worker process. ``designer_store`` marks a sample or test run failed
    when its task gives up."""

    extraction: RunExtractionService
    deep_analysis: RunDeepAnalysisService
    designer: DesignerWorkService
    designer_store: DesignerWorkStorePort

    def designer_store_failed_sample(self, sample_id: UUID) -> None:
        self.designer_store.sample_failed(sample_id, UnexpectedFailureError.code)

    def designer_store_failed_run(self, run_id: UUID) -> None:
        self.designer_store.fail_test_run(run_id, UnexpectedFailureError.code)


@dataclass(slots=True)
class _Binding:
    """The worker process's services, set once in ``worker_process_init`` (CEL-07)."""

    provider: Callable[[], WorkerServices] | None = None
    soft_time_limit_seconds: float = 0.0

    def services(self) -> WorkerServices:
        if self.provider is None:
            raise RuntimeError("the worker started without bind_services(); see extraction.worker")
        return self.provider()

    def service(self) -> RunExtractionService:
        return self.services().extraction


_binding = _Binding()


def bind_services(provider: Callable[[], WorkerServices], settings: CelerySettings) -> None:
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
            Queue(name, Exchange(name, type="topic"), routing_key=name, queue_arguments={"x-queue-type": "quorum"})
            for name in (PIPELINE_QUEUE, AI_QUEUE)
        ],
        task_default_queue_type="quorum",
        worker_detect_quorum_queues=True,
        broker_transport_options={"confirm_publish": True},
        broker_connection_retry_on_startup=True,
        task_ignore_result=True,
        result_backend=None,  # the database is the truth, not Celery results
        task_annotations={
            name: {"max_retries": settings.max_retries}
            for name in (TASK_NAME, DEEP_ANALYSIS_TASK, PREPARE_SAMPLE_TASK, TEST_RUN_TASK)
        },
        worker_hijack_root_logger=False,  # keep the structlog setup
    )
    return app


class CeleryTaskQueue:
    """``TaskQueuePort``: sends the task by name, so the consumer never imports worker code."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    def enqueue(self, item_id: UUID, kind: JobKind = JobKind.EXTRACTION) -> None:
        if kind is JobKind.DEEP_ANALYSIS:
            self._app.send_task(DEEP_ANALYSIS_TASK, args=[str(item_id)], queue=AI_QUEUE)
        else:
            self._app.send_task(TASK_NAME, args=[str(item_id)], queue=PIPELINE_QUEUE)

    def send(self, task: str, task_id: UUID) -> None:
        """A template sample or test run (``TemplateTasksPort``)."""
        self._app.send_task(task, args=[str(task_id)], queue=PIPELINE_QUEUE)


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


class _DeepAnalysisTask(_TaskBase):
    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        item_id = UUID(str(args[0]))
        log.error("deep_analysis_task_failed", item_id=str(item_id), error=repr(exc), exc_info=exc)
        _binding.services().deep_analysis.fail(item_id, UnexpectedFailureError())


@shared_task(
    name=DEEP_ANALYSIS_TASK,
    bind=True,
    base=_DeepAnalysisTask,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def run_deep_analysis(self: _DeepAnalysisTask, item_id: str) -> None:
    service = _binding.services().deep_analysis
    try:
        service.run(RunExtractionCommand(item_id=UUID(item_id)))
    except SoftTimeLimitExceeded:
        service.fail(UUID(item_id), ExtractionTimeoutError(_binding.soft_time_limit_seconds))


class _SampleTask(_TaskBase):
    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        log.error("prepare_sample_task_failed", sample_id=str(args[0]), error=repr(exc), exc_info=exc)
        _binding.services().designer_store_failed_sample(UUID(str(args[0])))


@shared_task(
    name=PREPARE_SAMPLE_TASK,
    base=_SampleTask,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def prepare_sample(sample_id: str) -> None:
    _binding.services().designer.prepare_sample(UUID(sample_id))


class _TestRunTask(_TaskBase):
    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        log.error("test_run_task_failed", run_id=str(args[0]), error=repr(exc), exc_info=exc)
        _binding.services().designer_store_failed_run(UUID(str(args[0])))


@shared_task(
    name=TEST_RUN_TASK,
    base=_TestRunTask,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def run_template_test(run_id: str) -> None:
    _binding.services().designer.run_test(UUID(run_id))
