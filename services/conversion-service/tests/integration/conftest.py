"""Real PostgreSQL, RabbitMQ and MinIO in containers, started once per test session (TST-02, TST-03).

Needs Docker. Tables are emptied after each test, so tests don't see each other's rows. MinIO's own images are
no longer published; the frozen Bitnami build is used for tests only (Java's team runs the real server).
"""

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from alembic import command
from alembic.config import Config
from mypy_boto3_s3 import S3Client
from pydantic import SecretStr
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from conversion.core.config import (
    CelerySettings,
    DatabaseSettings,
    RabbitSettings,
    Settings,
    StorageSettings,
)
from conversion.core.db import SessionFactory, create_db_engine, create_session_factory

SERVICE_ROOT = Path(__file__).resolve().parents[2]
MINIO_USER, MINIO_PASSWORD = "test-access-key", "test-secret-key"
BUCKETS = ("idp-originals", "idp-outputs")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.attributes["url"] = url
        command.upgrade(config, "head")
        yield url


@pytest.fixture(scope="session")
def rabbit_url() -> Iterator[str]:
    with RabbitMqContainer("rabbitmq:4.1-management-alpine") as rabbit:
        host, port = rabbit.get_container_host_ip(), rabbit.get_exposed_port(rabbit.port)
        yield f"amqp://guest:guest@{host}:{port}/"


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[str]:
    container = (
        DockerContainer("bitnamilegacy/minio:2025.4.22")
        .with_env("MINIO_ROOT_USER", MINIO_USER)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_PASSWORD)
        .with_exposed_ports(9000)
        .waiting_for(HttpWaitStrategy(9000, "/minio/health/ready"))
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"


@pytest.fixture(scope="session")
def database_settings(postgres_url: str) -> DatabaseSettings:
    return DatabaseSettings(url=SecretStr(postgres_url))


@pytest.fixture(scope="session")
def session_factory(database_settings: DatabaseSettings) -> SessionFactory:
    return create_session_factory(create_db_engine(database_settings, application_name="conversion-tests"))


@pytest.fixture
def sessions(session_factory: SessionFactory) -> Iterator[SessionFactory]:
    yield session_factory
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE conversion_item, outbox_event, inbox_message"))


@pytest.fixture(scope="session")
def storage_settings(s3: S3Client, minio_endpoint: str) -> StorageSettings:  # s3 creates the buckets
    return StorageSettings(
        endpoint=minio_endpoint,  # type: ignore[arg-type]  # pydantic validates the string into HttpUrl
        access_key=SecretStr(MINIO_USER),
        secret_key=SecretStr(MINIO_PASSWORD),
    )


@pytest.fixture(scope="session")
def s3(minio_endpoint: str) -> S3Client:
    client = boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        aws_access_key_id=MINIO_USER,
        aws_secret_access_key=MINIO_PASSWORD,
        region_name="us-east-1",
    )
    for bucket in BUCKETS:
        client.create_bucket(Bucket=bucket)
    return client


@pytest.fixture(scope="session")
def settings(database_settings: DatabaseSettings, rabbit_url: str, storage_settings: StorageSettings) -> Settings:
    return Settings(
        _env_file=None,
        db=database_settings,
        rabbit=RabbitSettings(url=SecretStr(rabbit_url)),
        celery=CelerySettings(broker_url=SecretStr(rabbit_url)),
        storage=storage_settings,
    )
