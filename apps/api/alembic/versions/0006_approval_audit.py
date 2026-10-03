"""Audit records for approval decisions, and a submitted purchase-order status.

Revision ID: 0006_approval_audit
Revises: 0005_draft_purchase_orders
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_approval_audit"
down_revision: str | None = "0005_draft_purchase_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_SUBMITTED = (
    "'open', 'received', 'cancelled', 'draft', 'waiting_approval', 'submitted'"
)
_WITHOUT_SUBMITTED = "'open', 'received', 'cancelled', 'draft', 'waiting_approval'"


def upgrade() -> None:
    op.drop_constraint("ck_purchase_orders_status", "purchase_orders", type_="check")
    op.create_check_constraint(
        "ck_purchase_orders_status",
        "purchase_orders",
        f"status IN ({_WITH_SUBMITTED})",
    )
    op.create_table(
        "audit_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("detail", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["operational_proposals.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
    )
    op.create_index(
        "ix_audit_records_proposal",
        "audit_records",
        ["tenant_id", "proposal_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_records_proposal", table_name="audit_records")
    op.drop_table("audit_records")
    op.execute("UPDATE purchase_orders SET status = 'waiting_approval' WHERE status = 'submitted'")
    op.drop_constraint("ck_purchase_orders_status", "purchase_orders", type_="check")
    op.create_check_constraint(
        "ck_purchase_orders_status",
        "purchase_orders",
        f"status IN ({_WITHOUT_SUBMITTED})",
    )
