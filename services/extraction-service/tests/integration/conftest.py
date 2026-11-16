"""Real PostgreSQL in a container, migrated with Alembic, shared by the session (TST-02). Needs Docker."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from extraction.core.config import DatabaseSettings
from extraction.core.db import (
    AsyncSessionFactory,
    SessionFactory,
    create_async_db_engine,
    create_async_session_factory,
    create_db_engine,
    create_session_factory,
)

SERVICE_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration
TABLES = (
    "template_samples",
    "template_test_runs",
    "template_versions",
    "templates",
    "document_types",
    "idempotency_record",
    "pipeline_runs",
    "extraction_item",
    "outbox_event",
    "inbox_message",
)


@pytest.fixture(scope="session")
def database() -> Iterator[DatabaseSettings]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.attributes["url"] = url
        command.upgrade(config, "head")
        yield DatabaseSettings(url=SecretStr(url))


@pytest.fixture(scope="session")
def sessions(database: DatabaseSettings) -> SessionFactory:
    return create_session_factory(create_db_engine(database, application_name="extraction-tests"))


@pytest.fixture
async def async_sessions(database: DatabaseSettings, sessions: SessionFactory) -> AsyncIterator[AsyncSessionFactory]:
    engine = create_async_db_engine(database, application_name="extraction-tests")
    yield create_async_session_factory(engine)
    await engine.dispose()
    with sessions.begin() as session:
        session.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
