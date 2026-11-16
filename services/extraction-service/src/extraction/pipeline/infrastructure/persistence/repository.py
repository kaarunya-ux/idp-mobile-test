"""The job item repository and the unit of work on PostgreSQL (standard ch. 8, OBX-01, MSG-02)."""

from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy.orm import Session

from extraction.core.clock import Clock
from extraction.core.db import SessionFactory
from extraction.core.ids import new_id
from extraction.core.inbox import record_once
from extraction.core.outbox import OutboxEventOrm
from extraction.pipeline.domain.jobs import ExtractionEvent, ExtractionItem
from extraction.pipeline.domain.runs import PipelineRun
from extraction.pipeline.infrastructure.messaging.mappers import to_outbox_row
from extraction.pipeline.infrastructure.persistence.mappers import to_domain, to_orm, update_orm
from extraction.pipeline.infrastructure.persistence.orm import ExtractionItemOrm, PipelineRunOrm


class SqlExtractionItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_update(self, item_id: UUID) -> ExtractionItem | None:
        row = self._session.get(ExtractionItemOrm, item_id, with_for_update=True)  # SELECT ... FOR UPDATE
        return to_domain(row) if row is not None else None

    def add(self, item: ExtractionItem) -> None:
        self._session.add(to_orm(item))

    def save(self, item: ExtractionItem) -> None:
        row = self._session.get(ExtractionItemOrm, item.id)  # already loaded and locked by get_for_update
        if row is None:
            self.add(item)
            return
        update_orm(row, item)


class SqlPipelineRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: PipelineRun) -> None:
        self._session.merge(  # a retried item that completes again replaces its row
            PipelineRunOrm(
                job_item_id=run.item_id,
                account_id=run.account_id,
                kind=run.kind,
                document_type_code=run.document_type,
                template_id=run.template_id,
                template_version=run.template_version,
                stages=dict(run.stages),
                engine_results=dict(run.decisions),
                ai_calls=run.ai_calls,
                duration_ms=run.duration_ms,
                created_at=run.created_at,
            )
        )


class SqlUnitOfWork:
    """One transaction. Leaving the ``with`` block without ``commit()`` rolls everything back."""

    items: SqlExtractionItemRepository
    runs: SqlPipelineRunRepository

    def __init__(self, sessions: SessionFactory, clock: Clock, *, producer: str) -> None:
        self._sessions = sessions
        self._clock = clock
        self._producer = producer
        self._session: Session | None = None

    def __enter__(self) -> Self:
        self._session = self._sessions()
        self._session.begin()
        self.items = SqlExtractionItemRepository(self._session)
        self.runs = SqlPipelineRunRepository(self._session)
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

    def add_event(self, event: ExtractionEvent) -> None:
        row = to_outbox_row(event, message_id=new_id(), occurred_at=self._clock.now(), producer=self._producer)
        self._open().add(OutboxEventOrm(**row))

    def commit(self) -> None:
        self._open().commit()

    def _open(self) -> Session:
        if self._session is None:
            raise RuntimeError("the unit of work is used outside its 'with' block")
        return self._session
