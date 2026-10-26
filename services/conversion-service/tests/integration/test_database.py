"""conversion_db on real PostgreSQL: the migration matches the code, and a unit of work commits all or nothing."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from conversion.conversion.domain.events import ItemContext
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, ItemStatus
from conversion.conversion.domain.values import InputFile, ObjectRef, StoredOutput
from conversion.conversion.infrastructure.persistence import orm  # noqa: F401 - registers the table
from conversion.conversion.infrastructure.persistence.repository import SqlUnitOfWork
from conversion.core.clock import SystemClock
from conversion.core.db import Base
from conversion.core.outbox import OutboxEventOrm

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def _item() -> ConversionItem:
    context = ItemContext(tenant_id=uuid4(), correlation_id="corr", job_id=uuid4(), item_id=uuid4())
    request = ConversionRequest(
        context=context,
        inputs=(InputFile(ref=ObjectRef(bucket="idp-originals", key="a/b/c"), format=Format.PDF),),
        target=Format.DOCX,
        options={"pageRange": "1-2", "dpi": 200},
        output_prefix="a/job/item",
    )
    return ConversionItem.receive(request, NOW)


def _unit(sessions) -> SqlUnitOfWork:
    return SqlUnitOfWork(sessions, SystemClock(), producer="conversion-service")


def test_the_migration_creates_exactly_the_tables_the_code_maps(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()

    assert differences == []


def test_stores_and_reads_back_an_item_with_its_request_and_result(sessions) -> None:
    item = _item()
    item.start(NOW)
    output = StoredOutput(
        ref=ObjectRef(bucket="idp-outputs", key="a/job/item/document.docx"),
        content_type="application/x",
        size=5,
        sha256="ff",
        page_count=None,
    )
    completed = item.complete((output,), 2, NOW)
    with _unit(sessions) as unit:
        unit.items.add(item)
        unit.add_event(completed)
        unit.commit()

    with _unit(sessions) as unit:
        loaded = unit.items.get_for_update(item.id)

    assert loaded is not None
    assert (loaded.status, loaded.attempts, loaded.sequence) == (ItemStatus.COMPLETED, 1, 2)
    assert loaded.request == item.request
    assert loaded.stored_completion() == completed
    with sessions() as session:
        (row,) = session.scalars(select(OutboxEventOrm)).all()
    assert (row.event_type, row.ordering_key, row.payload["payload"]["usage"]) == (
        "ConversionCompleted",
        str(item.id),
        {"pages": 2},
    )


def test_records_a_message_id_once_when_the_same_message_arrives_twice(sessions) -> None:
    message_id = uuid4()

    with _unit(sessions) as unit:
        first = unit.record_message_once(message_id, "ConversionRequested")
        unit.commit()
    with _unit(sessions) as unit:
        second = unit.record_message_once(message_id, "ConversionRequested")
        unit.commit()

    assert (first, second) == (True, False)


def test_keeps_nothing_when_the_unit_of_work_ends_without_commit(sessions) -> None:
    item = _item()

    with _unit(sessions) as unit:
        unit.items.add(item)
        unit.record_message_once(uuid4(), "ConversionRequested")

    with _unit(sessions) as unit:
        assert unit.items.get_for_update(item.id) is None
    with sessions() as session:
        assert session.execute(text("SELECT count(*) FROM inbox_message")).scalar_one() == 0


def test_saves_changes_to_an_item_it_loaded(sessions) -> None:
    item = _item()
    with _unit(sessions) as unit:
        unit.items.add(item)
        unit.commit()

    with _unit(sessions) as unit:
        loaded = unit.items.get_for_update(item.id)
        assert loaded is not None
        loaded.start(NOW)
        unit.items.save(loaded)
        unit.commit()

    with _unit(sessions) as unit:
        again = unit.items.get_for_update(item.id)
    assert again is not None
    assert again.status is ItemStatus.RUNNING


def test_refuses_an_unknown_status_in_the_database(sessions) -> None:
    item = _item()
    with _unit(sessions) as unit:
        unit.items.add(item)
        unit.commit()

    with pytest.raises(IntegrityError, match="ck_conversion_item__status"), sessions.begin() as session:
        session.execute(text("UPDATE conversion_item SET status = 'LOST'"))


def test_refuses_use_outside_the_with_block(sessions) -> None:
    unit = _unit(sessions)

    with pytest.raises(RuntimeError, match="outside"):
        unit.commit()
