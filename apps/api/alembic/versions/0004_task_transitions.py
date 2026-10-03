"""Persist task retries, workflow reasons, and status transitions.

Revision ID: 0004_task_transitions
Revises: 0003_supervisor_plan
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_task_transitions"
down_revision: str | None = "0003_supervisor_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_STATUSES = (
    "'PENDING', 'READY', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'SKIPPED'"
)


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("reason", sa.String(length=64), nullable=True))
    op.add_column(
        "agent_tasks",
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_agent_tasks_retries",
        "agent_tasks",
        "retry_count >= 0 AND retry_count <= 2",
    )
    op.create_table(
        "task_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.CheckConstraint(
            f"from_status IN ({_TASK_STATUSES})",
            name="ck_task_transitions_from",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_TASK_STATUSES})",
            name="ck_task_transitions_to",
        ),
    )
    op.create_index("ix_task_transitions_task", "task_transitions", ["tenant_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_transitions_task", table_name="task_transitions")
    op.drop_table("task_transitions")
    op.drop_constraint("ck_agent_tasks_retries", "agent_tasks", type_="check")
    op.drop_column("agent_tasks", "retry_count")
    op.drop_column("workflow_runs", "reason")
