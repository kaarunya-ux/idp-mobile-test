"""The inbox: remembers every message already handled, so a redelivered one is ignored (MSG-02).

Java sends each message with an ``eventId`` header: the id of the row in its own outbox. RabbitMQ can deliver the
same message twice (a lost acknowledgement, a consumer restart); a deliberate retry from Java is a new outbox row
with a new id (IDP-02 ch. 3.4). So "seen this id before" means "already handled". Same table as the Java
standard's ch. 27.5, so both languages mean the same thing by it.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from conversion.core.db import Base


class InboxMessageOrm(Base):
    __tablename__ = "inbox_message"

    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    message_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def record_once(session: Session, *, message_id: UUID, message_type: str) -> bool:
    """Note the message in the current transaction. ``False`` means it was handled before: acknowledge and skip."""
    statement = (
        insert(InboxMessageOrm)
        .values(message_id=message_id, message_type=message_type)
        .on_conflict_do_nothing(index_elements=[InboxMessageOrm.message_id])
        .returning(InboxMessageOrm.message_id)
    )
    return session.execute(statement).scalar_one_or_none() is not None
