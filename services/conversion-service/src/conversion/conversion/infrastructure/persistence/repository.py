"""The job item repository and the unit of work on PostgreSQL (standard ch. 8, OBX-01, MSG-02)."""

from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy.orm import Session

from conversion.conversion.domain.events import ConversionEvent
from conversion.conversion.domain.models import ConversionItem
from conversion.conversion.infrastructure.messaging.mappers import to_outbox_row
from conversion.conversion.infrastructure.persistence.mappers import to_domain, to_orm, update_orm
from conversion.conversion.infrastructure.persistence.orm import ConversionItemOrm
from conversion.core.clock import Clock
from conversion.core.db import SessionFactory
from conversion.core.ids import new_id
from conversion.core.inbox import record_once
from conversion.core.outbox import OutboxEventOrm


class SqlConversionItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_update(self, item_id: UUID) -> ConversionItem | None:
        row = self._session.get(ConversionItemOrm, item_id, with_for_update=True)  # SELECT ... FOR UPDATE
        return to_domain(row) if row is not None else None

    def add(self, item: ConversionItem) -> None:
        self._session.add(to_orm(item))

    def save(self, item: ConversionItem) -> None:
        row = self._session.get(ConversionItemOrm, item.id)  # already loaded and locked by get_for_update
        if row is None:
            self.add(item)
            return
        update_orm(row, item)


class SqlUnitOfWork:
    """One transaction. Leaving the ``with`` block without ``commit()`` rolls everything back."""

    items: SqlConversionItemRepository

    def __init__(self, sessions: SessionFactory, clock: Clock, *, producer: str) -> None:
        self._sessions = sessions
        self._clock = clock
        self._producer = producer
        self._session: Session | None = None

    def __enter__(self) -> Self:
        self._session = self._sessions()
        self._session.begin()
        self.items = SqlConversionItemRepository(self._session)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        session = self._open()
        if session.in_transaction():
            session.rollback()
        session.close()
        self._session = None

    def record_message_once(self, message_id: UUID, message_type: str) -> bool:
        return record_once(self._open(), message_id=message_id, message_type=message_type)

    def add_event(self, event: ConversionEvent) -> None:
        row = to_outbox_row(event, message_id=new_id(), occurred_at=self._clock.now(), producer=self._producer)
        self._open().add(OutboxEventOrm(**row))

    def commit(self) -> None:
        self._open().commit()

    def _open(self) -> Session:
        if self._session is None:
            raise RuntimeError("the unit of work is used outside its 'with' block")
        return self._session
