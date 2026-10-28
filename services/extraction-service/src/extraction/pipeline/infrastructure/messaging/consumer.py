"""Receives ``ExtractionRequested`` and ``DeepAnalysisRequested`` (IDP-00 ch. 7.6) and hands them to
``AcceptExtractionService``; receives ``AccountDeleted`` and archives the account's templates.

Command queues (Java standard ch. 27.2, standard ch. 16.3): ``extraction-service.extraction-requested.priority`` /
``.standard`` and ``extraction-service.deep-analysis-requested``; the event queue
``extraction-service.account-deleted`` is bound to ``idp.events``. For each:

- the queue: a quorum queue bound to its exchange, with a delivery
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
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from faststream import AckPolicy
from faststream.rabbit import ExchangeType, QueueType, RabbitBroker, RabbitExchange, RabbitQueue
from faststream.rabbit.annotations import RabbitMessage
from pydantic import BaseModel, ValidationError

from extraction.core.config import RabbitSettings
from extraction.core.errors import DomainError
from extraction.pipeline.application.commands import AcceptExtractionCommand, RejectExtractionCommand
from extraction.pipeline.application.services.accept_extraction import AcceptExtractionService
from extraction.pipeline.domain.jobs import ItemContext
from extraction.pipeline.infrastructure.messaging.mappers import (
    event_id_of,
    to_accept_command,
    to_deep_analysis_command,
)
from extraction.pipeline.infrastructure.messaging.messages import (
    COMMANDS_EXCHANGE,
    DEEP_ANALYSIS_REQUESTED,
    EVENTS_EXCHANGE,
    EXTRACTION_REQUESTED,
    AccountDeletedMessage,
    DeepAnalysisRequestedMessage,
    EnvelopeMessage,
    ExtractionRequestedMessage,
    ItemRequestMessage,
)

log = structlog.get_logger(__name__)

QUEUE_PREFIX = "extraction-service.extraction-requested"
DEEP_ANALYSIS_QUEUE = "extraction-service.deep-analysis-requested"
ACCOUNT_DELETED_QUEUE = "extraction-service.account-deleted"
ROUTES = ("priority", "standard")
RETRY_DELAYS = (timedelta(seconds=10), timedelta(minutes=1), timedelta(minutes=10))
RETRY_COUNT_HEADER = "x-retry-count"
COMMANDS = RabbitExchange(COMMANDS_EXCHANGE, type=ExchangeType.TOPIC, durable=True)
EVENTS = RabbitExchange(EVENTS_EXCHANGE, type=ExchangeType.TOPIC, durable=True)

type AccountDeletedHandler = Callable[[UUID], Awaitable[None]]


class UnusableMessageError(Exception):
    """The message can't be read at all; it goes to the dead-letter queue."""


@dataclass(frozen=True, slots=True)
class _Route:
    queue: str
    routing_key: str
    exchange: RabbitExchange


def queue_name(route: str) -> str:
    return f"{QUEUE_PREFIX}.{route}"


def routes() -> list[_Route]:
    work = [_Route(queue_name(r), f"extraction.requested.{r}", COMMANDS) for r in ROUTES]
    return [
        *work,
        _Route(DEEP_ANALYSIS_QUEUE, "extraction.deep-analysis.requested", COMMANDS),
        _Route(ACCOUNT_DELETED_QUEUE, "account.deleted", EVENTS),
    ]


def retry_queue_name(queue: str, delay: timedelta) -> str:
    seconds = int(delay.total_seconds())
    label = f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"
    return f"{queue}.retry.{label}"


def _queue(route: _Route, settings: RabbitSettings) -> RabbitQueue:
    return RabbitQueue(
        route.queue,
        queue_type=QueueType.QUORUM,
        durable=True,
        routing_key=route.routing_key,
        arguments={"x-delivery-limit": settings.delivery_limit, "x-dead-letter-exchange": f"{route.queue}.dlx"},
    )


def work_queue(route: str, settings: RabbitSettings) -> RabbitQueue:
    return _queue(_Route(queue_name(route), f"extraction.requested.{route}", COMMANDS), settings)


async def declare_topology(broker: RabbitBroker) -> None:
    """Dead-letter and retry queues. The work queues are declared by their subscribers."""
    for route in routes():
        name = route.queue
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


def register_consumers(
    broker: RabbitBroker,
    service: AcceptExtractionService,
    settings: RabbitSettings,
    *,
    derived_bucket: str,
    on_account_deleted: AccountDeletedHandler | None = None,
) -> None:
    """Subscribe to every queue. A failed message is rejected, never requeued (the dead-letter queue takes it)."""
    for route in routes():
        if route.queue == ACCOUNT_DELETED_QUEUE:
            if on_account_deleted is None:
                continue
            handler = _account_handler(route.queue, on_account_deleted, broker)
        else:
            handler = _command_handler(route.queue, service, broker, derived_bucket)
        broker.subscriber(_queue(route, settings), route.exchange, ack_policy=AckPolicy.REJECT_ON_ERROR)(handler)


def _command_handler(
    queue: str, service: AcceptExtractionService, broker: RabbitBroker, derived_bucket: str
) -> Callable[[RabbitMessage], Awaitable[None]]:
    """One handler per queue. Its only parameter is the raw message: FastStream would read any other as a body field."""

    async def on_request(message: RabbitMessage) -> None:
        await handle(message, service, broker, queue, derived_bucket=derived_bucket)

    return on_request


def _account_handler(
    queue: str, deleted: AccountDeletedHandler, broker: RabbitBroker
) -> Callable[[RabbitMessage], Awaitable[None]]:
    async def on_account_deleted(message: RabbitMessage) -> None:
        envelope, payload = _read(message.body, AccountDeletedMessage)
        headers: dict[str, Any] = dict(message.headers or {})
        try:
            await deleted(payload.account_id)  # archiving twice is harmless: no inbox needed
        except Exception as exc:  # the database is down: wait in a retry queue (MSG-03)
            await _retry_later(message, headers, broker, queue, exc)
            return
        log.info("account_deleted_handled", account_id=str(payload.account_id), message_id=str(envelope.message_id))

    return on_account_deleted


async def handle(
    message: RabbitMessage,
    service: AcceptExtractionService,
    broker: RabbitBroker,
    queue: str,
    *,
    derived_bucket: str = "idp-derived",
) -> None:
    deep = queue == DEEP_ANALYSIS_QUEUE
    kind: type[ItemRequestMessage] = DeepAnalysisRequestedMessage if deep else ExtractionRequestedMessage
    envelope, payload = _read(message.body, kind)
    headers: dict[str, Any] = dict(message.headers or {})
    structlog.contextvars.bind_contextvars(correlation_id=envelope.correlation_id, item_id=str(payload.item_id))
    try:
        event_id = event_id_of(headers, envelope)
        try:
            command = _command(envelope, payload, event_id, queue, derived_bucket)
        except DomainError as error:
            await asyncio.to_thread(service.reject, _rejection(envelope, payload, event_id, error, deep=deep))
            return
        try:
            await asyncio.to_thread(service.accept, command)  # sync database work off the event loop (ASY-01)
        except Exception as exc:  # a dependency is down: wait in a retry queue (MSG-03)
            await _retry_later(message, headers, broker, queue, exc)
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id", "item_id")


def _command(
    envelope: EnvelopeMessage, payload: ItemRequestMessage, event_id: UUID, queue: str, derived_bucket: str
) -> AcceptExtractionCommand:
    if isinstance(payload, DeepAnalysisRequestedMessage):
        return to_deep_analysis_command(envelope, payload, event_id=event_id, derived_bucket=derived_bucket)
    if not isinstance(payload, ExtractionRequestedMessage):
        raise TypeError(type(payload).__name__)
    return to_accept_command(envelope, payload, event_id=event_id, priority=queue == queue_name("priority"))


def _rejection(
    envelope: EnvelopeMessage, payload: ItemRequestMessage, event_id: UUID, error: DomainError, *, deep: bool
) -> RejectExtractionCommand:
    item = ItemContext(
        tenant_id=envelope.tenant_id,
        correlation_id=envelope.correlation_id,
        job_id=payload.job_id,
        item_id=payload.item_id,
    )
    message_type = DEEP_ANALYSIS_REQUESTED if deep else EXTRACTION_REQUESTED
    return RejectExtractionCommand(message_id=event_id, message_type=message_type, item=item, error=error)


def _read[M: BaseModel](body: bytes, model: type[M]) -> tuple[EnvelopeMessage, M]:
    try:
        envelope = EnvelopeMessage.model_validate_json(body)
        return envelope, model.model_validate(envelope.payload)
    except ValidationError as exc:
        log.error("message_unreadable", type=model.__name__, errors=exc.error_count())  # no body: it may hold PII
        raise UnusableMessageError from exc


async def _retry_later(
    message: RabbitMessage, headers: dict[str, Any], broker: RabbitBroker, queue: str, exc: Exception
) -> None:
    retries = int(headers.get(RETRY_COUNT_HEADER, 0))
    if retries >= len(RETRY_DELAYS):
        log.error("message_gave_up", queue=queue, retries=retries, error=repr(exc))
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
    log.warning("message_retry_scheduled", queue=target, retries=retries + 1, error=repr(exc))
