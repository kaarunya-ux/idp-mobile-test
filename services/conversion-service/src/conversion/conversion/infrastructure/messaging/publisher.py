"""Publishes outbox rows to ``idp.events`` for the relay (OBX-01; Java standard ch. 27.3 and 27.4).

Every message is persistent and mandatory, and the call waits for RabbitMQ's publisher confirm: an unconfirmed or
unroutable message raises, so the relay retries it instead of marking it ``SENT``. AMQP properties carry the
envelope's ids (``message_id``, ``correlation_id``, ``type``) plus the ``eventId`` header Java dedupes on.
"""

import json
from datetime import timedelta

from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange
from pamqp.commands import Basic

from conversion.conversion.infrastructure.messaging.messages import EVENT_ID_HEADER, EVENT_ROUTING_KEYS, EVENTS_EXCHANGE
from conversion.core.outbox import OutboxRecord

EVENTS = RabbitExchange(EVENTS_EXCHANGE, type=ExchangeType.TOPIC, durable=True)


class PublishNotConfirmedError(RuntimeError):
    """RabbitMQ didn't confirm the message (nack, or nothing bound to receive it)."""


class RabbitEventPublisher:
    def __init__(self, broker: RabbitBroker, *, timeout: timedelta = timedelta(seconds=10)) -> None:
        self._broker = broker
        self._timeout = timeout

    async def publish(self, record: OutboxRecord) -> None:
        confirmation = await self._broker.publish(
            json.dumps(record.payload, separators=(",", ":")).encode(),
            exchange=EVENTS,
            routing_key=EVENT_ROUTING_KEYS[record.event_type],
            message_id=str(record.id),
            correlation_id=record.correlation_id,
            message_type=record.event_type,
            headers={EVENT_ID_HEADER: str(record.id)},
            content_type="application/json",
            persist=True,
            mandatory=True,
            timeout=self._timeout.total_seconds(),
        )
        if not isinstance(confirmation, Basic.Ack):
            raise PublishNotConfirmedError(f"{record.event_type} {record.id}: {type(confirmation).__name__}")
