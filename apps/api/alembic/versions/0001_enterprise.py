"""Enterprise simulator tables.

Revision ID: 0001_enterprise
Revises:
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_enterprise"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
    )
    op.create_table(
        "budgets",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("available_cents", sa.Integer(), nullable=False),
        sa.Column("committed_cents", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint("available_cents >= 0", name="ck_budgets_available"),
        sa.CheckConstraint("committed_cents >= 0", name="ck_budgets_committed"),
    )
    op.create_table(
        "suppliers",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("supplier_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="ck_suppliers_reliability",
        ),
        sa.CheckConstraint("status IN ('approved', 'unapproved')", name="ck_suppliers_status"),
    )
    op.create_table(
        "supplier_terms",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("supplier_id", sa.String(length=64), primary_key=True),
        sa.Column("sku", sa.String(length=64), primary_key=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=False),
        sa.Column("case_pack", sa.Integer(), nullable=False),
        sa.Column("unit_cost_cents", sa.Integer(), nullable=False),
        sa.Column("preferred", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_supplier_terms_lead_time"),
        sa.CheckConstraint("minimum_order_quantity >= 1", name="ck_supplier_terms_moq"),
        sa.CheckConstraint("case_pack >= 1", name="ck_supplier_terms_case_pack"),
        sa.CheckConstraint("unit_cost_cents >= 0", name="ck_supplier_terms_unit_cost"),
    )
    op.create_table(
        "supplier_delays",
        sa.Column("delay_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("delay_days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        sa.CheckConstraint("delay_days >= 1", name="ck_supplier_delays_days"),
    )
    op.create_index(
        "ix_supplier_delays_tenant_supplier",
        "supplier_delays",
        ["tenant_id", "supplier_id"],
    )
    op.create_table(
        "sku_stocks",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("sku", sa.String(length=64), primary_key=True),
        sa.Column("region", sa.String(length=64), primary_key=True),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=False),
        sa.Column("safety_stock", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint("on_hand >= 0", name="ck_sku_stocks_on_hand"),
        sa.CheckConstraint("reserved >= 0", name="ck_sku_stocks_reserved"),
        sa.CheckConstraint("reorder_point >= 0", name="ck_sku_stocks_reorder_point"),
        sa.CheckConstraint("safety_stock >= 0", name="ck_sku_stocks_safety_stock"),
    )
    op.create_table(
        "stockout_events",
        sa.Column("stockout_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("units_short", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint("units_short >= 0", name="ck_stockout_events_units_short"),
    )
    op.create_index(
        "ix_stockout_events_lookup",
        "stockout_events",
        ["tenant_id", "sku", "region"],
    )
    op.create_table(
        "forecasts",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("sku", sa.String(length=64), primary_key=True),
        sa.Column("region", sa.String(length=64), primary_key=True),
        sa.Column("horizon_days", sa.Integer(), primary_key=True),
        sa.Column("p10", sa.Integer(), nullable=False),
        sa.Column("p50", sa.Integer(), nullable=False),
        sa.Column("p90", sa.Integer(), nullable=False),
        sa.Column("trend", sa.String(length=32), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("seasonality_label", sa.String(length=64), nullable=False),
        sa.Column("seasonality_index", sa.Float(), nullable=False),
        sa.Column("promotion_active", sa.Boolean(), nullable=False),
        sa.Column("promotion_name", sa.String(length=200), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint("horizon_days >= 1", name="ck_forecasts_horizon"),
        sa.CheckConstraint("p10 >= 0 AND p50 >= 0 AND p90 >= 0", name="ck_forecasts_quantiles"),
        sa.CheckConstraint("quality >= 0 AND quality <= 1", name="ck_forecasts_quality"),
        sa.CheckConstraint("trend IN ('rising', 'stable', 'falling')", name="ck_forecasts_trend"),
        sa.UniqueConstraint(
            "tenant_id",
            "sku",
            "region",
            "horizon_days",
            name="uq_forecasts_horizon",
        ),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("purchase_order_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expected_receipt_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_purchase_orders_quantity"),
        sa.CheckConstraint(
            "status IN ('open', 'received', 'cancelled')",
            name="ck_purchase_orders_status",
        ),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_purchase_orders_tenant_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_table("forecasts")
    op.drop_index("ix_stockout_events_lookup", table_name="stockout_events")
    op.drop_table("stockout_events")
    op.drop_table("sku_stocks")
    op.drop_index("ix_supplier_delays_tenant_supplier", table_name="supplier_delays")
    op.drop_table("supplier_delays")
    op.drop_table("supplier_terms")
    op.drop_table("suppliers")
    op.drop_table("budgets")
    op.drop_table("tenants")
