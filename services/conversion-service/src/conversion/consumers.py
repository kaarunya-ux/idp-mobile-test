"""Message consumer entry point, the ``intake`` process (IDP-03 ch. 3.2):

    faststream run conversion.consumers:create_app --factory

It records each ``ConversionRequested`` and queues the work; it never converts anything itself.
"""

from faststream import FastStream

from conversion.bootstrap import build_accept_service, build_rabbit_broker, build_session_factory
from conversion.conversion.infrastructure.messaging.consumer import declare_topology, register_consumers
from conversion.conversion.infrastructure.tasks import create_celery_app
from conversion.core.config import Settings, get_settings
from conversion.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastStream:
    settings = settings or get_settings()
    configure_logging(settings.logging)
    broker = build_rabbit_broker(settings)
    service = build_accept_service(
        settings, build_session_factory(settings), create_celery_app(settings.require_celery())
    )
    register_consumers(broker, service, settings.require_rabbit())
    app = FastStream(broker)

    @app.after_startup
    async def declare_dead_letter_and_retry_queues() -> None:
        await declare_topology(broker)

    return app
