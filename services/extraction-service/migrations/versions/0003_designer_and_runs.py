"""Template samples, test-run results, and ``pipeline_runs`` (IDP-03 ch. 2.4 and 2.9; build step E6).

Written by hand (MIG-02). Only new tables and new columns with defaults, so old and new code can run side by side
(MIG-05). ``template_test_runs`` was created empty in 0002; it gets the columns the designer needs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("expected", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("page_key", sa.Text(), nullable=True),
        sa.Column("words_key", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_template_samples"),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            name="fk_template_samples__template_version_id__template_versions",
        ),
        sa.CheckConstraint("status IN ('PENDING', 'READY', 'FAILED')", name="ck_template_samples__status"),
    )
    op.create_index("idx_template_samples__template_version_id", "template_samples", ["template_version_id"])
    op.add_column("template_test_runs", sa.Column("definition_hash", sa.String(64), server_default="", nullable=False))
    op.add_column("template_test_runs", sa.Column("error_code", sa.String(32), nullable=True))
    op.add_column("template_test_runs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_template_test_runs__status",
        "template_test_runs",
        "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
    )
    op.create_index(
        "idx_template_test_runs__template_version_id_created_at",
        "template_test_runs",
        ["template_version_id", "created_at"],
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("job_item_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("document_type_code", sa.String(64), nullable=True),
        sa.Column("template_id", sa.String(64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("stages", postgresql.JSONB(), nullable=False),
        sa.Column("engine_results", postgresql.JSONB(), nullable=False),
        sa.Column("ai_calls", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_item_id", name="pk_pipeline_runs"),
    )
    op.create_index("idx_pipeline_runs__account_id", "pipeline_runs", ["account_id"])
    op.create_index("idx_pipeline_runs__created_at", "pipeline_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_pipeline_runs__created_at", table_name="pipeline_runs")
    op.drop_index("idx_pipeline_runs__account_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("idx_template_test_runs__template_version_id_created_at", table_name="template_test_runs")
    op.drop_constraint("ck_template_test_runs__status", "template_test_runs", type_="check")
    op.drop_column("template_test_runs", "finished_at")
    op.drop_column("template_test_runs", "error_code")
    op.drop_column("template_test_runs", "definition_hash")
    op.drop_index("idx_template_samples__template_version_id", table_name="template_samples")
    op.drop_table("template_samples")
