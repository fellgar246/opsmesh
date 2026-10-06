"""Evaluation runs recorded for the console."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_evaluation_runs"
down_revision: str | None = "0007_event_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("suite_version", sa.String(length=32), nullable=False),
        sa.Column("suite", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.CheckConstraint("passed >= 0", name="ck_evaluation_runs_passed"),
        sa.CheckConstraint("failed >= 0", name="ck_evaluation_runs_failed"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
    )
    op.create_index(
        "ix_evaluation_runs_tenant_created",
        "evaluation_runs",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_tenant_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
