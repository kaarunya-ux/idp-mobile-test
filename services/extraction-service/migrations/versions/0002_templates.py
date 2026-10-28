"""Templates and document types (IDP-03 ch. 2.4), and the idempotency records of the API (RTR-08).

Written by hand (MIG-02). Only new tables, so old and new code can run side by side (MIG-05). ``pipeline_runs``
comes with the metrics step (E6).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("classifier_hints", postgresql.JSONB(), nullable=False),
        sa.Column("default_template_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_types"),
        sa.UniqueConstraint(
            "account_id", "code", name="uq_document_types__account_id_code", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("document_type_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_templates"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_templates__status"),
    )
    op.create_index("idx_templates__account_id_document_type_code", "templates", ["account_id", "document_type_code"])
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("sample_keys", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(128), nullable=True),
        sa.Column("publish_reason", sa.Text(), nullable=True),
        sa.Column("accuracy", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_template_versions"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], name="fk_template_versions__template_id__templates"),
        sa.UniqueConstraint("template_id", "number", name="uq_template_versions__template_id_number"),
        sa.CheckConstraint("state IN ('DRAFT', 'TESTING', 'PUBLISHED', 'RETIRED')", name="ck_template_versions__state"),
    )
    op.create_index(
        "uq_template_versions__one_published",
        "template_versions",
        ["template_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PUBLISHED'"),
    )
    op.create_table(
        "template_test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("samples", postgresql.JSONB(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_template_test_runs"),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            name="fk_template_test_runs__template_version_id__template_versions",
        ),
    )
    op.create_table(
        "idempotency_record",
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner", "key", name="pk_idempotency_record"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_record")
    op.drop_table("template_test_runs")
    op.drop_index("uq_template_versions__one_published", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index("idx_templates__account_id_document_type_code", table_name="templates")
    op.drop_table("templates")
    op.drop_table("document_types")
