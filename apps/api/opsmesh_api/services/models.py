"""Persistence for the local enterprise simulator."""

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from opsmesh_api.db import Base


class SupplierStatus(StrEnum):
    APPROVED = "approved"
    UNAPPROVED = "unapproved"


class PurchaseOrderStatus(StrEnum):
    OPEN = "open"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class Trend(StrEnum):
    RISING = "rising"
    STABLE = "stable"
    FALLING = "falling"


class UtcDateTime(TypeDecorator[datetime]):
    """Persist timezone-aware timestamps and return them as UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Tenant(Base):
    """Isolation boundary for simulator records."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Budget(Base):
    """Purchasing budget for one tenant, in integer cents."""

    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint("available_cents >= 0", name="ck_budgets_available"),
        CheckConstraint("committed_cents >= 0", name="ck_budgets_committed"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    available_cents: Mapped[int]
    committed_cents: Mapped[int]
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Supplier(Base):
    """Supplier identity, approval status, and reliability within a tenant."""

    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="ck_suppliers_reliability",
        ),
        CheckConstraint(
            "status IN ('approved', 'unapproved')",
            name="ck_suppliers_status",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    reliability_score: Mapped[float] = mapped_column(Float)


class SupplierTerms(Base):
    """Commercial terms for one supplier and SKU."""

    __tablename__ = "supplier_terms"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        CheckConstraint("lead_time_days >= 0", name="ck_supplier_terms_lead_time"),
        CheckConstraint("minimum_order_quantity >= 1", name="ck_supplier_terms_moq"),
        CheckConstraint("case_pack >= 1", name="ck_supplier_terms_case_pack"),
        CheckConstraint("unit_cost_cents >= 0", name="ck_supplier_terms_unit_cost"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_time_days: Mapped[int]
    minimum_order_quantity: Mapped[int]
    case_pack: Mapped[int]
    unit_cost_cents: Mapped[int]
    preferred: Mapped[bool]


class SupplierDelay(Base):
    """A delay recorded against a supplier. Active delays are the ones reads return."""

    __tablename__ = "supplier_delays"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        CheckConstraint("delay_days >= 1", name="ck_supplier_delays_days"),
        Index("ix_supplier_delays_tenant_supplier", "tenant_id", "supplier_id"),
    )

    delay_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    supplier_id: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool]
    delay_days: Mapped[int]
    reason: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SkuStock(Base):
    """On-hand and reserved units for a SKU in a region."""

    __tablename__ = "sku_stocks"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_sku_stocks_on_hand"),
        CheckConstraint("reserved >= 0", name="ck_sku_stocks_reserved"),
        CheckConstraint("reorder_point >= 0", name="ck_sku_stocks_reorder_point"),
        CheckConstraint("safety_stock >= 0", name="ck_sku_stocks_safety_stock"),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    on_hand: Mapped[int]
    reserved: Mapped[int]
    available: Mapped[int]
    reorder_point: Mapped[int]
    safety_stock: Mapped[int]
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class StockoutEvent(Base):
    """A historical stockout for a SKU and region."""

    __tablename__ = "stockout_events"
    __table_args__ = (
        CheckConstraint("units_short >= 0", name="ck_stockout_events_units_short"),
        Index("ix_stockout_events_lookup", "tenant_id", "sku", "region"),
    )

    stockout_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    sku: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    units_short: Mapped[int]


class Forecast(Base):
    """Fixed forecast quantiles for a SKU, region, and horizon."""

    __tablename__ = "forecasts"
    __table_args__ = (
        CheckConstraint("horizon_days >= 1", name="ck_forecasts_horizon"),
        CheckConstraint("p10 >= 0 AND p50 >= 0 AND p90 >= 0", name="ck_forecasts_quantiles"),
        CheckConstraint("quality >= 0 AND quality <= 1", name="ck_forecasts_quality"),
        CheckConstraint("trend IN ('rising', 'stable', 'falling')", name="ck_forecasts_trend"),
        UniqueConstraint(
            "tenant_id",
            "sku",
            "region",
            "horizon_days",
            name="uq_forecasts_horizon",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    horizon_days: Mapped[int] = mapped_column(primary_key=True)
    p10: Mapped[int]
    p50: Mapped[int]
    p90: Mapped[int]
    trend: Mapped[str] = mapped_column(String(32))
    quality: Mapped[float] = mapped_column(Float)
    seasonality_label: Mapped[str] = mapped_column(String(64))
    seasonality_index: Mapped[float] = mapped_column(Float)
    promotion_active: Mapped[bool]
    promotion_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PurchaseOrder(Base):
    """A purchase order stored only inside the simulator."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.supplier_id"],
        ),
        CheckConstraint("quantity >= 1", name="ck_purchase_orders_quantity"),
        CheckConstraint(
            "status IN ('open', 'received', 'cancelled')",
            name="ck_purchase_orders_status",
        ),
        Index("ix_purchase_orders_tenant_id", "tenant_id"),
    )

    purchase_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    supplier_id: Mapped[str] = mapped_column(String(64))
    sku: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int]
    status: Mapped[str] = mapped_column(String(32))
    expected_receipt_on: Mapped[date] = mapped_column(Date)
