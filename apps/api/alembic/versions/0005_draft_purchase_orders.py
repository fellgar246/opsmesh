"""Draft purchase orders and idempotency records.

Revision ID: 0005_draft_purchase_orders
Revises: 0004_task_transitions
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_draft_purchase_orders"
down_revision: str | None = "0004_task_transitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DRAFT_STATUSES = "'open', 'received', 'cancelled', 'draft', 'waiting_approval'"
_SEED_STATUSES = "'open', 'received', 'cancelled'"


def upgrade() -> None:
    op.drop_constraint("ck_purchase_orders_status", "purchase_orders", type_="check")
    op.create_check_constraint(
        "ck_purchase_orders_status",
        "purchase_orders",
        f"status IN ({_DRAFT_STATUSES})",
    )
    op.add_column("purchase_orders", sa.Column("proposal_id", sa.Uuid(), nullable=True))
    op.add_column("purchase_orders", sa.Column("unit_cost_cents", sa.Integer(), nullable=True))
    op.add_column(
        "purchase_orders",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("arguments_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_purchase_orders_unit_cost",
        "purchase_orders",
        "unit_cost_cents IS NULL OR unit_cost_cents >= 0",
    )
    op.create_foreign_key(
        "fk_purchase_orders_proposal",
        "purchase_orders",
        "operational_proposals",
        ["proposal_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_purchase_orders_idempotency",
        "purchase_orders",
        ["tenant_id", "idempotency_key"],
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("result_reference", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["result_reference"],
            ["purchase_orders.purchase_order_id"],
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_tenant_key"),
    )
    op.create_index("ix_idempotency_records_tenant", "idempotency_records", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_tenant", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.execute("DELETE FROM purchase_orders WHERE status IN ('draft', 'waiting_approval')")
    op.drop_constraint("uq_purchase_orders_idempotency", "purchase_orders", type_="unique")
    op.drop_constraint("fk_purchase_orders_proposal", "purchase_orders", type_="foreignkey")
    op.drop_constraint("ck_purchase_orders_unit_cost", "purchase_orders", type_="check")
    op.drop_column("purchase_orders", "arguments_hash")
    op.drop_column("purchase_orders", "idempotency_key")
    op.drop_column("purchase_orders", "unit_cost_cents")
    op.drop_column("purchase_orders", "proposal_id")
    op.drop_constraint("ck_purchase_orders_status", "purchase_orders", type_="check")
    op.create_check_constraint(
        "ck_purchase_orders_status",
        "purchase_orders",
        f"status IN ({_SEED_STATUSES})",
    )
