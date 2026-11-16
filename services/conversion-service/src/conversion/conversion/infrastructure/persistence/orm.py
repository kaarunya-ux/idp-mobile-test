"""Table mapping for job items (DAO-01, DAO-02). ``*Orm`` objects never leave this package (OBJ-02)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from conversion.core.db import Base


class ConversionItemOrm(Base):
    """One job item. ``item_id`` is document-service's id, so a repeat of the same item finds this row."""

    __tablename__ = "conversion_item"
    __table_args__ = (CheckConstraint("status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')", name="status"),)

    item_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    job_id: Mapped[UUID]
    correlation_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # DB-05: enums as text
    attempts: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)  # validated by mappers.StoredRequest (DB-09)
    completion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
