"""Approval wait state for one proposal.

Revision ID: 0009_approval_process
Revises: 0008_evaluation_runs
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_approval_process"
down_revision: str | None = "0008_evaluation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = "'waiting', 'approved', 'rejected', 'expired', 'draft_created', 'notified'"
_SIGNALS = "'approve', 'reject', 'timeout'"


def upgrade() -> None:
    op.create_table(
        "approval_processes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_signal", sa.String(length=16), nullable=True),
        sa.Column("arguments_hash", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_approval_processes_state"),
        sa.CheckConstraint(
            f"last_signal IS NULL OR last_signal IN ({_SIGNALS})",
            name="ck_approval_processes_signal",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["operational_proposals.id"]),
        sa.UniqueConstraint("tenant_id", "proposal_id", name="uq_approval_processes_proposal"),
    )
    op.create_index(
        "ix_approval_processes_proposal",
        "approval_processes",
        ["tenant_id", "proposal_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_approval_processes_proposal", table_name="approval_processes")
    op.drop_table("approval_processes")
