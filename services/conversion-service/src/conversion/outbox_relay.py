"""Outbox relay entry point, the ``relay`` process (IDP-03 ch. 3.2; OBX-01):

    python -m conversion.outbox_relay

Publishes outbox rows to RabbitMQ until stopped. Run one or two: ``SKIP LOCKED`` keeps them from sending the
same row. SIGTERM finishes the current batch, then exits (DPL-04).
"""

import asyncio
import signal

import structlog

from conversion.bootstrap import build_outbox_relay, build_rabbit_broker, build_session_factory
from conversion.core.config import Settings, get_settings
from conversion.core.logging import configure_logging

log = structlog.get_logger(__name__)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    broker = build_rabbit_broker(settings)
    relay = build_outbox_relay(settings, build_session_factory(settings), broker)
    await broker.connect()
    log.info("outbox_relay_started")
    try:
        await relay.run_forever(stop)
    finally:
        await broker.stop()
        log.info("outbox_relay_stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.logging)

    async def run_until_signalled() -> None:
        stop, loop = asyncio.Event(), asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await run(settings, stop)

    asyncio.run(run_until_signalled())


if __name__ == "__main__":
    main()
