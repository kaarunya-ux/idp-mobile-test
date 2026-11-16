"""The outbox relay on real PostgreSQL: order per item, retries with backoff, dead rows, and expired claims."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from conversion.core.clock import SystemClock
from conversion.core.config import OutboxSettings
from conversion.core.db import SessionFactory
from conversion.core.outbox import OutboxEventOrm, OutboxRecord, OutboxRelay, OutboxStatus


class RecordingPublisher:
    def __init__(self, *, failing: set[UUID] | None = None) -> None:
        self.sent: list[UUID] = []
        self.failing = failing or set()

    async def publish(self, record: OutboxRecord) -> None:
        if record.id in self.failing:
            raise ConnectionError("broker down")
        self.sent.append(record.id)


def _add(sessions: SessionFactory, key: str, *, created: int = 0) -> UUID:
    row_id = uuid4()
    with sessions.begin() as session:
        session.add(
            OutboxEventOrm(
                id=row_id,
                tenant_id=uuid4(),
                aggregate_type="ConversionItem",
                aggregate_id=uuid4(),
                ordering_key=key,
                aggregate_version=created,
                event_type="JobProgressed",
                payload={"messageId": str(row_id)},
                correlation_id="corr",
                created_at=datetime(2026, 10, 26, 9, 0, created, tzinfo=UTC),
            )
        )
    return row_id


def _row(sessions: SessionFactory, row_id: UUID) -> OutboxEventOrm:
    with sessions() as session:
        return session.scalars(select(OutboxEventOrm).where(OutboxEventOrm.id == row_id)).one()


def _relay(sessions: SessionFactory, publisher: RecordingPublisher, **settings: object) -> OutboxRelay:
    return OutboxRelay(sessions, publisher, OutboxSettings(**settings), SystemClock(), instance_id="test-relay")


async def test_sends_pending_rows_oldest_first_and_marks_them_sent(sessions) -> None:
    second, first = _add(sessions, "a", created=2), _add(sessions, "b", created=1)
    publisher = RecordingPublisher()

    sent = await _relay(sessions, publisher).run_once()

    assert (sent, publisher.sent) == (2, [first, second])
    assert {_row(sessions, first).status, _row(sessions, second).status} == {OutboxStatus.SENT}
    assert _row(sessions, first).sent_at is not None


async def test_holds_back_later_rows_of_an_item_when_an_earlier_one_fails(sessions) -> None:
    failed, later, other = (
        _add(sessions, "a", created=1),
        _add(sessions, "a", created=2),
        _add(sessions, "b", created=3),
    )
    publisher = RecordingPublisher(failing={failed})

    await _relay(sessions, publisher).run_once()

    failed_row, later_row = _row(sessions, failed), _row(sessions, later)
    assert publisher.sent == [other]
    assert (failed_row.status, failed_row.attempts) == (OutboxStatus.RETRYING, 1)
    assert failed_row.available_at > datetime.now(UTC)
    assert "broker down" in (failed_row.last_error or "")
    assert (later_row.status, later_row.attempts) == (OutboxStatus.PENDING, 0)
    assert await _relay(sessions, RecordingPublisher()).run_once() == 0  # still blocked by the RETRYING row


async def test_retries_a_retrying_row_once_its_backoff_has_passed(sessions) -> None:
    failed, later = _add(sessions, "a", created=1), _add(sessions, "a", created=2)
    await _relay(sessions, RecordingPublisher(failing={failed})).run_once()
    with sessions.begin() as session:  # the backoff has passed
        session.execute(
            update(OutboxEventOrm)
            .where(OutboxEventOrm.id == failed)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    publisher = RecordingPublisher()

    await _relay(sessions, publisher).run_once()  # the RETRYING row isn't blocked by itself (the Java query bug)
    await _relay(sessions, publisher).run_once()

    assert publisher.sent == [failed, later]
    assert _row(sessions, failed).attempts == 2


async def test_marks_a_row_dead_after_the_last_attempt(sessions) -> None:
    row_id = _add(sessions, "a")

    await _relay(sessions, RecordingPublisher(failing={row_id}), max_attempts=1).run_once()

    assert _row(sessions, row_id).status == OutboxStatus.DEAD


def test_does_not_give_a_second_relay_the_rows_another_has_claimed(sessions) -> None:
    _add(sessions, "a")
    _add(sessions, "b")

    first = _relay(sessions, RecordingPublisher()).claim()
    second = _relay(sessions, RecordingPublisher()).claim()

    assert (len(first), second) == (2, [])


@pytest.mark.parametrize(("claimed_ago", "expected"), [(timedelta(minutes=5), "PENDING"), (timedelta(0), "CLAIMED")])
def test_returns_rows_to_pending_only_when_their_claim_has_expired(sessions, claimed_ago, expected) -> None:
    row_id = _add(sessions, "a")
    relay = _relay(sessions, RecordingPublisher())
    relay.claim()
    with sessions.begin() as session:
        session.execute(
            update(OutboxEventOrm).where(OutboxEventOrm.id == row_id).values(claimed_at=datetime.now(UTC) - claimed_ago)
        )

    relay.release_expired_claims()

    assert _row(sessions, row_id).status == expected
