"""End to end on real PostgreSQL, RabbitMQ and MinIO (TST-02): ``ExtractionRequested`` on ``idp.commands`` -> the
intake consumer records the item -> the worker runs it (a fake pipeline: no OCR models in CI) -> ``result.json`` in
MinIO -> the outbox relay publishes ``JobProgressed`` and ``ExtractionCompleted`` on ``idp.events``.

Celery is the one piece left out: the consumer's task queue is a recorder, and the test runs the worker's service
directly, as the task would."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import aio_pika
import pytest
from faststream.rabbit import RabbitBroker
from pydantic import SecretStr
from sqlalchemy import select
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from extraction.bootstrap import build_s3_client, build_units
from extraction.core.clock import SystemClock
from extraction.core.config import OutboxSettings, RabbitSettings, StorageSettings
from extraction.core.outbox import OutboxRelay
from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.application.services.accept_extraction import AcceptExtractionService
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.jobs import JobKind, ObjectRef
from extraction.pipeline.infrastructure.messaging.consumer import COMMANDS, declare_topology, register_consumers
from extraction.pipeline.infrastructure.messaging.messages import EVENTS_EXCHANGE
from extraction.pipeline.infrastructure.messaging.publisher import RabbitEventPublisher
from extraction.pipeline.infrastructure.persistence.orm import PipelineRunOrm
from extraction.pipeline.infrastructure.storage.s3_storage import S3ObjectStorage
from support.pipeline_fakes import Pipeline

pytestmark = pytest.mark.integration
TENANT, DOC = uuid4(), uuid4()
MINIO_USER, MINIO_PASSWORD = "test-access-key", "test-secret-key"


# The same containers as conversion-service's integration tests, which run on the team's machines.
@pytest.fixture(scope="module")
def rabbit_url() -> Iterator[str]:
    with RabbitMqContainer("rabbitmq:4.1-management-alpine") as rabbit:
        yield f"amqp://guest:guest@{rabbit.get_container_host_ip()}:{rabbit.get_exposed_port(rabbit.port)}/"


@pytest.fixture(scope="module")
def storage() -> Iterator[StorageSettings]:
    container = (
        DockerContainer("bitnamilegacy/minio:2025.4.22")
        .with_env("MINIO_ROOT_USER", MINIO_USER)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_PASSWORD)
        .with_exposed_ports(9000)
        .waiting_for(HttpWaitStrategy(9000, "/minio/health/ready"))
    )
    with container:
        settings = StorageSettings.model_validate(
            {
                "endpoint": f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}",
                "access_key": MINIO_USER,
                "secret_key": MINIO_PASSWORD,
            }
        )
        client = build_s3_client(settings)
        for bucket in ("idp-originals", "idp-derived"):
            client.create_bucket(Bucket=bucket)
        yield settings


class Recorder:
    def __init__(self) -> None:
        self.queued: list[tuple[UUID, JobKind]] = []

    def enqueue(self, item_id: UUID, kind: JobKind = JobKind.EXTRACTION) -> None:
        self.queued.append((item_id, kind))


@pytest.fixture
async def events(rabbit_url: str) -> AsyncIterator[aio_pika.abc.AbstractQueue]:
    connection = await aio_pika.connect_robust(rabbit_url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(EVENTS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue(exclusive=True)
    await queue.bind(exchange, routing_key="#")
    yield queue
    await connection.close()


def _request(item_id: UUID, key: str) -> bytes:
    payload = {
        "jobId": str(uuid4()),
        "itemId": str(item_id),
        "documentId": str(DOC),
        "files": [{"bucket": "idp-originals", "key": key, "contentType": "application/pdf"}],
        "templateId": "auto",
    }
    envelope = {
        "messageId": str(uuid4()),
        "type": "ExtractionRequested",
        "version": 1,
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "document-service",
        "correlationId": "corr-e2e",
        "tenantId": str(TENANT),
        "subjectId": str(item_id),
        "payload": payload,
    }
    return json.dumps(envelope).encode()


async def _wait_for(condition, seconds: float = 15.0) -> None:
    for _ in range(int(seconds * 10)):
        if condition():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("timed out")


async def test_takes_a_request_through_intake_worker_storage_and_outbox(
    rabbit_url: str, storage: StorageSettings, sessions, events
) -> None:
    clock, recorder, item_id = SystemClock(), Recorder(), uuid4()
    units = build_units(sessions, clock)
    objects = S3ObjectStorage(build_s3_client(storage), max_input_size=10_000_000)
    key = f"{TENANT}/{DOC}/file-1"
    objects.write_bytes(_ref("idp-originals", key), b"%PDF-1.7 fake", "application/pdf")
    broker = RabbitBroker(rabbit_url)
    accept = AcceptExtractionService(units, recorder, clock, stale_after=_minutes(12))
    register_consumers(broker, accept, RabbitSettings(url=SecretStr(rabbit_url)), derived_bucket="idp-derived")
    await broker.start()
    try:
        await declare_topology(broker)
        await broker.publish(_request(item_id, key), exchange=COMMANDS, routing_key="extraction.requested.standard")
        await _wait_for(lambda: recorder.queued)

        worker = RunExtractionService(
            units, objects, objects, Pipeline(), clock, derived_bucket="idp-derived", stale_after=_minutes(12)
        )
        await asyncio.to_thread(worker.run, RunExtractionCommand(item_id=item_id))
        relay = OutboxRelay(sessions, RabbitEventPublisher(broker), OutboxSettings(), clock, instance_id="e2e")
        sent = await relay.run_once()
    finally:
        await broker.stop()

    types = [json.loads((await events.get(timeout=5)).body)["type"] for _ in range(sent)]
    assert recorder.queued == [(item_id, JobKind.EXTRACTION)]
    assert types[-1] == "ExtractionCompleted"
    assert set(types[:-1]) == {"JobProgressed"}
    result_key = f"{TENANT}/{DOC}/{item_id}/result.json"
    assert json.loads(objects.read_bytes(_ref("idp-derived", result_key)))["documentType"]["code"] == "PAN_CARD"
    with sessions() as session:
        assert session.scalar(select(PipelineRunOrm.ai_calls).where(PipelineRunOrm.job_item_id == item_id)) == 1


def _ref(bucket: str, key: str) -> ObjectRef:
    return ObjectRef(bucket=bucket, key=key)


def _minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
