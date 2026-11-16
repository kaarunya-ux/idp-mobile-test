"""The intake consumer on fake messages: routing, rejection, retry queues, and AccountDeleted."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from extraction.pipeline.domain.jobs import JobKind
from extraction.pipeline.infrastructure.messaging import consumer
from extraction.pipeline.infrastructure.messaging.consumer import (
    ACCOUNT_DELETED_QUEUE,
    DEEP_ANALYSIS_QUEUE,
    RETRY_COUNT_HEADER,
    UnusableMessageError,
    handle,
    queue_name,
)

TENANT, DOC = uuid4(), uuid4()


@dataclass
class Message:
    body: bytes
    headers: dict[str, Any] = field(default_factory=dict)
    message_id: str = "m-1"
    correlation_id: str = "corr-1"
    content_type: str = "application/json"


class Broker:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.subscribed: list[str] = []

    async def publish(self, body: bytes, *, queue: str, headers: dict[str, Any], **_: object) -> None:
        self.published.append((queue, headers))

    def subscriber(self, queue: Any, exchange: Any, **_: object):
        self.subscribed.append(queue.name)
        return lambda handler: handler


class Service:
    def __init__(self, *, down: bool = False) -> None:
        self.down, self.accepted, self.rejected = down, [], []

    def accept(self, command) -> None:
        if self.down:
            raise ConnectionError("database down")
        self.accepted.append(command)

    def reject(self, command) -> None:
        self.rejected.append(command)


def _body(message_type: str, payload: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "messageId": str(uuid4()),
            "type": message_type,
            "version": 1,
            "occurredAt": datetime(2026, 10, 28, tzinfo=UTC).isoformat(),
            "producer": "document-service",
            "correlationId": "corr-1",
            "tenantId": str(TENANT),
            "subjectId": "s",
            "payload": payload,
        }
    ).encode()


def _extraction(**changes: object) -> bytes:
    payload = {
        "jobId": str(uuid4()),
        "itemId": str(uuid4()),
        "documentId": str(DOC),
        "files": [{"bucket": "idp-originals", "key": f"{TENANT}/{DOC}/f", "contentType": "application/pdf"}],
        **changes,
    }
    return _body("ExtractionRequested", payload)


async def test_accepts_with_the_routes_priority_when_a_request_arrives() -> None:
    service = Service()

    await handle(Message(_extraction()), service, Broker(), queue_name("priority"))

    assert service.accepted[0].request.priority is True


async def test_accepts_deep_analysis_from_its_own_queue() -> None:
    service = Service()
    payload = {
        "jobId": str(uuid4()),
        "itemId": str(uuid4()),
        "resultKey": f"{TENANT}/{DOC}/{uuid4()}/result.json",
        "pageKeys": [f"{TENANT}/{DOC}/x/page-0.png"],
    }

    await handle(Message(_body("DeepAnalysisRequested", payload)), service, Broker(), DEEP_ANALYSIS_QUEUE)

    assert service.accepted[0].request.kind is JobKind.DEEP_ANALYSIS


async def test_rejects_with_work_failed_when_the_request_cannot_be_used() -> None:
    service = Service()
    body = _extraction(files=[{"bucket": "idp-originals", "key": "k", "contentType": "text/plain"}])

    await handle(Message(body), service, Broker(), queue_name("standard"))

    assert service.accepted == []
    assert service.rejected[0].message_type == "ExtractionRequested"


async def test_moves_the_message_to_the_next_retry_queue_when_the_database_is_down() -> None:
    broker = Broker()

    await handle(Message(_extraction(), {RETRY_COUNT_HEADER: 1}), Service(down=True), broker, queue_name("standard"))

    queue, headers = broker.published[0]
    assert queue == f"{queue_name('standard')}.retry.1m"
    assert headers[RETRY_COUNT_HEADER] == 2


async def test_gives_up_to_the_dead_letter_queue_after_the_last_retry() -> None:
    with pytest.raises(ConnectionError):
        await handle(
            Message(_extraction(), {RETRY_COUNT_HEADER: 3}), Service(down=True), Broker(), queue_name("standard")
        )


async def test_raises_unusable_when_the_message_has_no_envelope() -> None:
    with pytest.raises(UnusableMessageError):
        await handle(Message(b"{}"), Service(), Broker(), queue_name("standard"))


async def test_archives_the_accounts_templates_when_it_is_deleted_and_retries_when_that_fails() -> None:
    deleted: list[UUID] = []

    async def on_deleted(account: UUID) -> None:
        deleted.append(account)

    async def broken(account: UUID) -> None:
        raise ConnectionError("down")

    account = uuid4()
    body = _body("AccountDeleted", {"accountId": str(account)})
    await consumer._account_handler(ACCOUNT_DELETED_QUEUE, on_deleted, Broker())(Message(body))
    broker = Broker()
    await consumer._account_handler(ACCOUNT_DELETED_QUEUE, broken, broker)(Message(body))

    assert deleted == [account]
    assert broker.published[0][0] == f"{ACCOUNT_DELETED_QUEUE}.retry.10s"


def test_subscribes_to_every_queue_and_skips_account_events_without_a_handler() -> None:
    from extraction.core.config import RabbitSettings  # noqa: PLC0415

    broker = Broker()
    settings = RabbitSettings.model_validate({"url": "amqp://guest:guest@localhost:5672/"})

    consumer.register_consumers(broker, Service(), settings, derived_bucket="idp-derived")

    assert ACCOUNT_DELETED_QUEUE not in broker.subscribed
    assert set(broker.subscribed) == {queue_name("priority"), queue_name("standard"), DEEP_ANALYSIS_QUEUE}
