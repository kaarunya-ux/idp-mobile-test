"""RabbitMQ: what we publish to ``idp.events``, and how ``ConversionRequested`` is consumed, dead-lettered and retried.

The last test runs the whole path: Java's message -> intake -> Celery worker -> MinIO -> outbox -> ``idp.events``.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustQueue
from celery.contrib.testing.worker import start_worker
from faststream.rabbit import RabbitBroker, RabbitQueue
from mypy_boto3_s3 import S3Client
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select

from conversion.bootstrap import build_accept_service, build_outbox_relay, build_process_service, build_units
from conversion.conversion.application.services.accept_conversion import AcceptConversionService
from conversion.conversion.domain.models import ItemStatus
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.domain.values import WorkerPool
from conversion.conversion.infrastructure import tasks
from conversion.conversion.infrastructure.messaging.consumer import (
    COMMANDS,
    RETRY_COUNT_HEADER,
    declare_topology,
    queue_name,
    register_consumers,
)
from conversion.conversion.infrastructure.messaging.publisher import (
    EVENTS,
    PublishNotConfirmedError,
    RabbitEventPublisher,
)
from conversion.conversion.infrastructure.persistence.orm import ConversionItemOrm
from conversion.core.clock import SystemClock
from conversion.core.config import RabbitSettings, Settings
from conversion.core.db import SessionFactory
from conversion.core.inbox import InboxMessageOrm
from conversion.core.outbox import OutboxRecord

STANDARD = queue_name("standard")


class RecordingTaskQueue:
    def __init__(self) -> None:
        self.queued: list[tuple[UUID, WorkerPool]] = []

    def enqueue(self, item_id: UUID, pool: WorkerPool) -> None:
        self.queued.append((item_id, pool))


def broken_units() -> Any:
    raise ConnectionError("database down")


def _request(item_id: UUID, *, key: str = "acc/doc/photo.png", target: str = "PDF") -> bytes:
    return json.dumps(
        {
            "messageId": str(uuid4()),
            "type": "ConversionRequested",
            "version": 1,
            "occurredAt": datetime.now(UTC).isoformat(),
            "producer": "document-service",
            "correlationId": "corr-it",
            "tenantId": str(uuid4()),
            "subjectId": str(item_id),
            "payload": {
                "jobId": str(uuid4()),
                "itemId": str(item_id),
                "type": "CONVERSION",
                "inputs": [{"bucket": "idp-originals", "key": key, "contentType": "image/png"}],
                "target": target,
                "options": {},
                "outputPrefix": f"acc/job/{item_id}",
            },
        }
    ).encode()


async def _send(broker: RabbitBroker, body: bytes, *, event_id: UUID | None = None) -> None:
    await broker.publish(
        body,
        exchange=COMMANDS,
        routing_key="conversion.requested.standard",
        headers={"eventId": str(event_id or uuid4())},
        persist=True,
    )


async def _until[T](check: Callable[[], Awaitable[T | None]], *, seconds: float = 20) -> T:
    async with asyncio.timeout(seconds):
        # RabbitMQ and the database can't signal us; poll them
        while (found := await check()) is None:  # noqa: ASYNC110
            await asyncio.sleep(0.2)
        return found


async def _get(queue: AbstractRobustQueue) -> AbstractIncomingMessage | None:
    message = await queue.get(fail=False)
    if message is not None:
        await message.ack()
    return message


async def _event_queue(broker: RabbitBroker, routing_key: str) -> AbstractRobustQueue:
    queue = await broker.declare_queue(RabbitQueue(f"test.{routing_key}.{uuid4()}", auto_delete=True))
    await queue.bind(await broker.declare_exchange(EVENTS), routing_key=routing_key)
    return queue


@pytest.fixture
async def broker(rabbit_url: str) -> AsyncIterator[RabbitBroker]:
    broker = RabbitBroker(rabbit_url)
    await broker.connect()
    yield broker
    await broker.stop()


@pytest.fixture
async def start_intake(rabbit_url: str) -> AsyncIterator[Callable[[AcceptConversionService], Awaitable[None]]]:
    """Starts the intake consumers with a given service; stops them after the test."""
    started: list[RabbitBroker] = []

    async def start(service: AcceptConversionService) -> None:
        intake = RabbitBroker(rabbit_url)
        register_consumers(intake, service, RabbitSettings(url=SecretStr(rabbit_url)))
        await intake.start()
        await declare_topology(intake)
        started.append(intake)

    yield start
    for intake in started:
        await intake.stop()


def _accept_service(sessions: SessionFactory, task_queue: RecordingTaskQueue) -> AcceptConversionService:
    clock = SystemClock()
    return AcceptConversionService(
        build_units(sessions, clock), task_queue, RoutePlanner(), clock, stale_after=timedelta(minutes=10)
    )


async def test_publishes_a_confirmed_persistent_message_with_the_ids_java_reads(broker: RabbitBroker) -> None:
    queue = await _event_queue(broker, "conversion.completed")
    record = OutboxRecord(
        id=uuid4(),
        event_type="ConversionCompleted",
        ordering_key="k",
        payload={"a": 1},
        correlation_id="c-1",
        attempts=1,
    )

    await RabbitEventPublisher(broker).publish(record)

    message = await _until(lambda: _get(queue))
    assert json.loads(message.body) == {"a": 1}
    assert (message.message_id, message.correlation_id, message.type) == (str(record.id), "c-1", "ConversionCompleted")
    assert (message.headers["eventId"], message.delivery_mode.value, message.content_type) == (
        str(record.id),
        2,
        "application/json",
    )


async def test_raises_when_nothing_is_bound_to_receive_the_message(broker: RabbitBroker) -> None:
    await broker.declare_exchange(EVENTS)
    record = OutboxRecord(
        id=uuid4(), event_type="JobProgressed", ordering_key="k", payload={}, correlation_id="c", attempts=1
    )

    with pytest.raises(PublishNotConfirmedError):
        await RabbitEventPublisher(broker).publish(record)


async def test_records_a_request_and_queues_it_once_even_when_it_arrives_twice(sessions, broker, start_intake) -> None:
    task_queue, item_id, event_id = RecordingTaskQueue(), uuid4(), uuid4()
    await start_intake(_accept_service(sessions, task_queue))

    body = _request(item_id)
    await _send(broker, body, event_id=event_id)
    await _send(broker, body, event_id=event_id)

    async def two_handled() -> int | None:
        with sessions() as session:
            item = session.get(ConversionItemOrm, item_id)
            inbox = session.scalar(select(func.count()).select_from(InboxMessageOrm))
        return inbox if item is not None and len(task_queue.queued) >= 2 else None

    assert await _until(two_handled) == 1  # one inbox row: the second copy was recognised by its eventId
    assert {pool for _, pool in task_queue.queued} == {WorkerPool.PDF}


async def test_sends_an_unreadable_message_to_the_dead_letter_queue(broker, start_intake, sessions) -> None:
    await start_intake(_accept_service(sessions, RecordingTaskQueue()))
    dlq = await broker.declare_queue(RabbitQueue(f"{STANDARD}.dlq", declare=False))  # already declared by the intake

    await _send(broker, b"not json")

    message = await _until(lambda: _get(dlq))
    assert message.body == b"not json"


async def test_moves_a_request_to_the_retry_queue_when_the_database_is_down(broker, start_intake) -> None:
    clock = SystemClock()
    service = AcceptConversionService(
        broken_units, RecordingTaskQueue(), RoutePlanner(), clock, stale_after=timedelta(minutes=10)
    )
    await start_intake(service)
    retry = await broker.declare_queue(
        RabbitQueue(f"{STANDARD}.retry.10s", declare=False)
    )  # already declared by the intake

    await _send(broker, _request(uuid4()))

    message = await _until(lambda: _get(retry))
    assert message.headers[RETRY_COUNT_HEADER] == 1


async def test_converts_a_requested_file_and_reports_completion_end_to_end(
    settings: Settings, sessions, broker, start_intake, s3: S3Client
) -> None:
    image = BytesIO()
    Image.new("RGB", (40, 30), "red").save(image, format="PNG")
    s3.put_object(Bucket="idp-originals", Key="acc/doc/e2e.png", Body=image.getvalue())
    events = await _event_queue(broker, "#")  # every event: an unroutable JobProgressed would hold back the rest
    celery_app = tasks.create_celery_app(settings.require_celery())
    service = build_process_service(settings, sessions)
    tasks.bind_service(lambda: service, settings.require_celery())
    relay = build_outbox_relay(settings, sessions, broker)
    item_id = uuid4()

    with start_worker(celery_app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        await start_intake(build_accept_service(settings, sessions, celery_app))
        await _send(broker, _request(item_id, key="acc/doc/e2e.png"))

        async def relayed_completion() -> AbstractIncomingMessage | None:
            await relay.run_once()
            while (message := await _get(events)) is not None:
                if message.type == "ConversionCompleted":
                    return message
            return None

        message = await _until(relayed_completion, seconds=60)

    envelope = json.loads(message.body)
    (output,) = envelope["payload"]["outputs"]
    assert (envelope["type"], envelope["subjectId"], output["contentType"]) == (
        "ConversionCompleted",
        str(item_id),
        "application/pdf",
    )
    assert s3.get_object(Bucket=output["bucket"], Key=output["key"])["Body"].read().startswith(b"%PDF")
    with sessions() as session:
        assert session.get(ConversionItemOrm, item_id).status == ItemStatus.COMPLETED.value  # type: ignore[union-attr]
