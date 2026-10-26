"""The outbox: outgoing messages are rows written with the state change, then published by a relay (OBX-01).

Writing the row in the same transaction as the change means "item COMPLETED" and "tell Java it's completed"
can't come apart: both happen or neither does. The relay (``python -m conversion.outbox_relay``) claims rows,
publishes each with a publisher confirm, and only then marks it ``SENT``. Table and claim query follow the Java
standard ch. 15.2, with two fixes: a ``RETRYING`` row is not blocked by itself (``b.id <> o.id``), and claimed
rows come back oldest first (``RETURNING`` alone keeps no order, so an item's messages could go out of order).

Row states: ``PENDING`` -> ``CLAIMED`` -> ``SENT``; a failed publish -> ``RETRYING`` (with backoff) -> ... -> ``DEAD``
after ``OUTBOX__MAX_ATTEMPTS`` (logged as an error for alerting, OBX-07).
"""

import asyncio
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from conversion.core.clock import Clock
from conversion.core.config import OutboxSettings
from conversion.core.db import Base, SessionFactory

log = structlog.get_logger(__name__)


class OutboxStatus:
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SENT = "SENT"
    RETRYING = "RETRYING"
    DEAD = "DEAD"


class OutboxEventOrm(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (
        Index(
            "idx_outbox_event__claimable",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'RETRYING')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)  # also the message's messageId and eventId header
    tenant_id: Mapped[UUID]
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[UUID]
    ordering_key: Mapped[str] = mapped_column(String(128))  # one message in flight per key, so order is kept
    aggregate_version: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the whole envelope, exactly as it will be sent
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxRecord:
    """A claimed row, ready to publish."""

    id: UUID
    event_type: str
    ordering_key: str
    payload: Mapping[str, Any]
    correlation_id: str
    attempts: int


class EventPublisherPort(Protocol):
    async def publish(self, record: OutboxRecord) -> None:
        """Publish and wait for the broker's confirm; raise if it isn't confirmed."""
        ...


# UPDATE ... RETURNING gives rows in no particular order, so the outer SELECT puts them back in creation order.
_CLAIM = text("""
WITH claimed AS (
UPDATE outbox_event
SET status = 'CLAIMED', claimed_by = :instance_id, claimed_at = now(), attempts = attempts + 1
WHERE id IN (
    SELECT o.id FROM outbox_event o
    WHERE o.status IN ('PENDING', 'RETRYING') AND o.available_at <= now()
      AND NOT EXISTS (
          SELECT 1 FROM outbox_event b
          WHERE b.ordering_key = o.ordering_key AND b.id <> o.id AND b.status IN ('CLAIMED', 'RETRYING'))
    ORDER BY o.created_at
    FOR UPDATE SKIP LOCKED
    LIMIT :batch_size)
RETURNING id, event_type, ordering_key, payload, correlation_id, attempts, created_at)
SELECT id, event_type, ordering_key, payload, correlation_id, attempts FROM claimed ORDER BY created_at
""")


class OutboxRelay:
    """Claims rows and publishes them. Several relays can run at once: ``SKIP LOCKED`` gives each its own rows."""

    def __init__(
        self,
        sessions: SessionFactory,
        publisher: EventPublisherPort,
        settings: OutboxSettings,
        clock: Clock,
        *,
        instance_id: str,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher
        self._settings = settings
        self._clock = clock
        self._instance_id = instance_id

    async def run_once(self) -> int:
        """Publish one batch; return how many rows were sent."""
        await asyncio.to_thread(self.release_expired_claims)
        records = await asyncio.to_thread(self.claim)
        sent, blocked = 0, set[str]()
        for record in records:
            if record.ordering_key in blocked:  # an earlier message of this item failed: keep the order
                await asyncio.to_thread(self._release, record.id)
                continue
            try:
                await self._publisher.publish(record)
            except Exception as exc:  # any publish failure is retried; the row records why
                blocked.add(record.ordering_key)
                await asyncio.to_thread(self._mark_failed, record, repr(exc))
            else:
                await asyncio.to_thread(self._mark_sent, record.id)
                sent += 1
        return sent

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not await self.run_once():  # idle: wait, unless asked to stop
                await _wait(stop, self._settings.poll_interval)

    def claim(self) -> list[OutboxRecord]:
        with self._sessions.begin() as session:
            rows = session.execute(
                _CLAIM, {"instance_id": self._instance_id, "batch_size": self._settings.batch_size}
            ).mappings()
            return [OutboxRecord(**row) for row in rows]

    def release_expired_claims(self) -> None:
        """Rows claimed by a relay that died go back to ``PENDING`` after the lease (OBX-03)."""
        cutoff = self._clock.now() - self._settings.lease
        with self._sessions.begin() as session:
            session.execute(
                update(OutboxEventOrm)
                .where(OutboxEventOrm.status == OutboxStatus.CLAIMED, OutboxEventOrm.claimed_at < cutoff)
                .values(status=OutboxStatus.PENDING, claimed_by=None, claimed_at=None)
            )

    def _mark_sent(self, record_id: UUID) -> None:
        with self._sessions.begin() as session:
            session.execute(
                update(OutboxEventOrm)
                .where(OutboxEventOrm.id == record_id)
                .values(status=OutboxStatus.SENT, sent_at=self._clock.now(), last_error=None)
            )

    def _mark_failed(self, record: OutboxRecord, error: str) -> None:
        dead = record.attempts >= self._settings.max_attempts
        values: dict[str, object] = {"last_error": error[:2000], "claimed_by": None, "claimed_at": None}
        if dead:
            values["status"] = OutboxStatus.DEAD
            log.error("outbox_event_dead", event_id=str(record.id), event_type=record.event_type, error=error)
        else:
            values["status"] = OutboxStatus.RETRYING
            values["available_at"] = self._clock.now() + self._backoff(record.attempts)
            log.warning("outbox_publish_failed", event_id=str(record.id), attempts=record.attempts, error=error)
        with self._sessions.begin() as session:
            session.execute(update(OutboxEventOrm).where(OutboxEventOrm.id == record.id).values(**values))

    def _release(self, record_id: UUID) -> None:
        with self._sessions.begin() as session:
            session.execute(
                update(OutboxEventOrm)
                .where(OutboxEventOrm.id == record_id)
                .values(
                    status=OutboxStatus.PENDING,
                    claimed_by=None,
                    claimed_at=None,
                    attempts=OutboxEventOrm.attempts - 1,  # not tried, so the attempt doesn't count
                )
            )

    def _backoff(self, attempts: int) -> timedelta:
        """Exponential with full jitter (OBX-07): up to 2, 4, 8 ... seconds, capped at ``OUTBOX__BACKOFF_MAX``."""
        cap_ms = int(min(self._settings.backoff_max.total_seconds(), 2.0**attempts) * 1000)
        return timedelta(milliseconds=secrets.randbelow(cap_ms) + 1)


async def _wait(stop: asyncio.Event, interval: timedelta) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=interval.total_seconds())
    except TimeoutError:
        return
