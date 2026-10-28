"""Tables of templates and document types (IDP-03 ch. 2.4; DAO-01, DAO-02). ``*Orm`` objects stay in this package."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from extraction.core.db import Base


class DocumentTypeOrm(Base):
    """``code`` is unique per account; a standard type has no account (NULLS NOT DISTINCT keeps one per code)."""

    __tablename__ = "document_types"
    __table_args__ = (UniqueConstraint("account_id", "code", postgresql_nulls_not_distinct=True),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID | None]
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32))
    classifier_hints: Mapped[dict[str, Any]] = mapped_column(JSONB)
    default_template_id: Mapped[UUID | None]
    active: Mapped[bool] = mapped_column(Boolean)


class TemplateOrm(Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="status"),
        Index(None, "account_id", "document_type_code"),  # DB-08: tenant first
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID | None]
    document_type_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    current_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)  # row version for optimistic locking
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    versions: Mapped[list["TemplateVersionOrm"]] = relationship(
        back_populates="template", order_by="TemplateVersionOrm.number", cascade="all, delete-orphan", lazy="selectin"
    )


class TemplateVersionOrm(Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "number"),
        CheckConstraint("state IN ('DRAFT', 'TESTING', 'PUBLISHED', 'RETIRED')", name="state"),
        # one PUBLISHED version per template (IDP-03 ch. 2.4)
        Index(
            "uq_template_versions__one_published",
            "template_id",
            unique=True,
            postgresql_where=text("state = 'PUBLISHED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("templates.id"))
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sample_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(128))
    publish_reason: Mapped[str | None] = mapped_column(Text)
    accuracy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    template: Mapped[TemplateOrm] = relationship(back_populates="versions")


class TemplateTestRunOrm(Base):
    """Filled by the designer's test runs (build step E6); the table exists so publish rules can read it then."""

    __tablename__ = "template_test_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    template_version_id: Mapped[UUID] = mapped_column(ForeignKey("template_versions.id"))
    status: Mapped[str] = mapped_column(String(16))
    samples: Mapped[dict[str, Any]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyRecordOrm(Base):
    """The first result of a non-idempotent write, by caller and key (RTR-08). Kept 24 hours (a cleanup job, later)."""

    __tablename__ = "idempotency_record"

    owner: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
