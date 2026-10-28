"""Receives ``ExtractionRequested`` (IDP-00 ch. 7.6) and hands it to ``AcceptExtractionService``.

Queues (Java standard ch. 27.2, standard ch. 16.3), declared for both the ``priority`` and ``standard`` routes:

- ``extraction-service.extraction-requested.<route>``: quorum queue bound to ``idp.commands``, with a delivery
  limit; anything rejected goes through ``<queue>.dlx`` to ``<queue>.dlq``, where it waits for a person (MSG-04).
- ``<queue>.retry.10s`` / ``.1m`` / ``.10m``: a message whose handling failed for a passing reason (the database
  or the task broker is down) waits there and then flows back to the queue. After the last one it is rejected
  to the dead-letter queue. Nothing is requeued in a hot loop (MSG-03).

What happens to a message:

======================================  =========================================================================
Message                                 Outcome
======================================  =========================================================================
not valid JSON / no envelope / no ids   rejected to the dead-letter queue: nobody to tell
valid, but unusable (job type, inputs)  acknowledged; ``WorkFailed`` sent so Java's item doesn't hang
valid                                   recorded, queued for a worker, acknowledged
handling hit a passing failure          copied to the next retry queue, acknowledged
======================================  =========================================================================
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import structlog
from faststream import AckPolicy
from faststream.rabbit import ExchangeType, QueueType, RabbitBroker, RabbitExchange, RabbitQueue
from faststream.rabbit.annotations import RabbitMessage
from pydantic import ValidationError

from extraction.core.config import RabbitSettings
from extraction.core.errors import DomainError
from extraction.pipeline.application.services.accept_extraction import AcceptExtractionService
from extraction.pipeline.infrastructure.messaging.mappers import event_id_of, to_accept_command, to_reject_command
from extraction.pipeline.infrastructure.messaging.messages import (
    COMMANDS_EXCHANGE,
    EnvelopeMessage,
    ExtractionRequestedMessage,
)

log = structlog.get_logger(__name__)

QUEUE_PREFIX = "extraction-service.extraction-requested"
ROUTES = ("priority", "standard")
RETRY_DELAYS = (timedelta(seconds=10), timedelta(minutes=1), timedelta(minutes=10))
RETRY_COUNT_HEADER = "x-retry-count"
COMMANDS = RabbitExchange(COMMANDS_EXCHANGE, type=ExchangeType.TOPIC, durable=True)


class UnusableMessageError(Exception):
    """The message can't be read at all; it goes to the dead-letter queue."""


def queue_name(route: str) -> str:
    return f"{QUEUE_PREFIX}.{route}"


def retry_queue_name(queue: str, delay: timedelta) -> str:
    seconds = int(delay.total_seconds())
    label = f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"
    return f"{queue}.retry.{label}"


def work_queue(route: str, settings: RabbitSettings) -> RabbitQueue:
    name = queue_name(route)
    return RabbitQueue(
        name,
        queue_type=QueueType.QUORUM,
        durable=True,
        routing_key=f"extraction.requested.{route}",
        arguments={"x-delivery-limit": settings.delivery_limit, "x-dead-letter-exchange": f"{name}.dlx"},
    )


async def declare_topology(broker: RabbitBroker) -> None:
    """Dead-letter and retry queues. The work queues are declared by their subscribers."""
    for route in ROUTES:
        name = queue_name(route)
        dlx = await broker.declare_exchange(RabbitExchange(f"{name}.dlx", type=ExchangeType.FANOUT, durable=True))
        dlq = await broker.declare_queue(RabbitQueue(f"{name}.dlq", queue_type=QueueType.QUORUM, durable=True))
        await dlq.bind(dlx)
        for delay in RETRY_DELAYS:
            await broker.declare_queue(
                RabbitQueue(
                    retry_queue_name(name, delay),
                    durable=True,
                    arguments={
                        "x-message-ttl": int(delay.total_seconds() * 1000),
                        "x-dead-letter-exchange": "",  # the default exchange routes by queue name...
                        "x-dead-letter-routing-key": name,  # ...back to the work queue
                    },
                )
            )


def register_consumers(broker: RabbitBroker, service: AcceptExtractionService, settings: RabbitSettings) -> None:
    """Subscribe to both routes. A failed message is rejected, never requeued (the dead-letter queue takes it)."""
    for route in ROUTES:
        queue = work_queue(route, settings)
        handler = _handler_for(queue.name, service, broker)
        broker.subscriber(queue, COMMANDS, ack_policy=AckPolicy.REJECT_ON_ERROR)(handler)


def _handler_for(
    queue: str, service: AcceptExtractionService, broker: RabbitBroker
) -> Callable[[RabbitMessage], Awaitable[None]]:
    """One handler per queue. Its only parameter is the raw message: FastStream would read any other as a body field."""

    async def on_extraction_requested(message: RabbitMessage) -> None:
        await handle(message, service, broker, queue)

    return on_extraction_requested


async def handle(message: RabbitMessage, service: AcceptExtractionService, broker: RabbitBroker, queue: str) -> None:
    envelope, payload = _read(message.body)
    headers: dict[str, Any] = dict(message.headers or {})
    structlog.contextvars.bind_contextvars(correlation_id=envelope.correlation_id, item_id=str(payload.item_id))
    try:
        event_id = event_id_of(headers, envelope)
        try:
            command = to_accept_command(envelope, payload, event_id=event_id, priority=queue == queue_name("priority"))
        except DomainError as error:
            await asyncio.to_thread(
                service.reject, to_reject_command(envelope, payload, event_id=event_id, error=error)
            )
            return
        try:
            await asyncio.to_thread(service.accept, command)  # sync database work off the event loop (ASY-01)
        except Exception as exc:  # a dependency is down: wait in a retry queue (MSG-03)
            await _retry_later(message, headers, broker, queue, exc)
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id", "item_id")


def _read(body: bytes) -> tuple[EnvelopeMessage, ExtractionRequestedMessage]:
    try:
        envelope = EnvelopeMessage.model_validate_json(body)
        return envelope, ExtractionRequestedMessage.model_validate(envelope.payload)
    except ValidationError as exc:
        log.error("extraction_request_unreadable", errors=exc.error_count())  # no body in logs: it may hold PII
        raise UnusableMessageError from exc


async def _retry_later(
    message: RabbitMessage, headers: dict[str, Any], broker: RabbitBroker, queue: str, exc: Exception
) -> None:
    retries = int(headers.get(RETRY_COUNT_HEADER, 0))
    if retries >= len(RETRY_DELAYS):
        log.error("extraction_request_gave_up", retries=retries, error=repr(exc))
        raise exc  # rejected to the dead-letter queue
    target = retry_queue_name(queue, RETRY_DELAYS[retries])
    await broker.publish(
        message.body,
        queue=target,
        headers={**headers, RETRY_COUNT_HEADER: retries + 1},
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        content_type=message.content_type,
        persist=True,
    )
    log.warning("extraction_request_retry_scheduled", queue=target, retries=retries + 1, error=repr(exc))
