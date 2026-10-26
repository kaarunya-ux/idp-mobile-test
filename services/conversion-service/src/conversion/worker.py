"""Celery worker entry point (IDP-03 ch. 3.2; standard ch. 23). One worker pool per queue:

    celery -A conversion.worker worker -Q office -c 2 --prefetch-multiplier 1 -n office@%h
    celery -A conversion.worker worker -Q pdf,data -c 4 --prefetch-multiplier 1 -n pdf-data@%h

Each worker process builds its database pool, storage client and converters once, when it starts (CEL-07).
"""

from celery.signals import worker_init, worker_process_init

from conversion.bootstrap import build_process_service, build_session_factory
from conversion.conversion.infrastructure import tasks
from conversion.core.config import get_settings
from conversion.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.logging)
celery_app = tasks.create_celery_app(settings.require_celery())


@worker_init.connect
def declare_every_pool_queue(**_: object) -> None:
    """Celery's delayed retries bind every pool's queue, not only the ones this worker serves, so all must exist."""
    with celery_app.connection_for_write() as connection:
        for queue in celery_app.conf.task_queues:
            queue.bind(connection.default_channel).declare()


@worker_process_init.connect
def build_services(**_: object) -> None:
    """Runs in every worker process after the fork, so no connection is shared between processes."""
    service = build_process_service(settings, build_session_factory(settings))
    tasks.bind_service(lambda: service, settings.require_celery())
