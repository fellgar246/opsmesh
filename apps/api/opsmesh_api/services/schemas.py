"""Response and fixture schemas for the local enterprise simulator."""

from datetime import UTC, date, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opsmesh_api.services.models import PurchaseOrderStatus, SupplierStatus, Trend


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeasonalityContext(APIModel):
    label: str = Field(min_length=1)
    index: float = Field(gt=0)


class PromotionContext(APIModel):
    active: bool
    name: str | None = None


class InventorySnapshot(APIModel):
    tenant_id: str
    sku: str
    region: str
    on_hand_units: int
    reserved_units: int
    available_units: int
    reorder_point_units: int
    observed_at: datetime


class ReorderParameters(APIModel):
    tenant_id: str
    sku: str
    region: str
    reorder_point_units: int
    safety_stock_units: int
    observed_at: datetime


class StockoutRead(APIModel):
    stockout_id: str
    tenant_id: str
    sku: str
    region: str
    started_at: datetime
    ended_at: datetime | None
    units_short: int


class StockoutList(APIModel):
    stockouts: list[StockoutRead]


class ForecastRead(APIModel):
    tenant_id: str
    sku: str
    region: str
    horizon_days: int
    p10: int
    p50: int
    p90: int
    trend: Trend
    quality: float = Field(ge=0, le=1)
    seasonality: SeasonalityContext
    promotion: PromotionContext
    observed_at: datetime


class ForecastQualityRead(APIModel):
    tenant_id: str
    sku: str
    region: str
    horizon_days: int
    quality: float = Field(ge=0, le=1)
    observed_at: datetime


class DemandDriversRead(APIModel):
    tenant_id: str
    sku: str
    region: str
    horizon_days: int
    trend: Trend
    seasonality: SeasonalityContext
    promotion: PromotionContext
    observed_at: datetime


class PreferredSupplierRead(APIModel):
    tenant_id: str
    supplier_id: str
    sku: str
    status: SupplierStatus


class SupplierTermsRead(APIModel):
    tenant_id: str
    supplier_id: str
    sku: str
    lead_time_days: int
    minimum_order_quantity: int
    case_pack: int
    unit_cost_cents: int
    status: SupplierStatus
    reliability_score: float = Field(ge=0, le=1)


class ReliabilityRead(APIModel):
    tenant_id: str
    supplier_id: str
    status: SupplierStatus
    reliability_score: float = Field(ge=0, le=1)


class SupplierDelayRead(APIModel):
    delay_id: str
    tenant_id: str
    supplier_id: str
    active: bool
    delay_days: int
    reason: str
    started_at: datetime


class BudgetRead(APIModel):
    tenant_id: str
    available_cents: int
    committed_cents: int
    observed_at: datetime


class CommittedSpendRead(APIModel):
    tenant_id: str
    committed_cents: int
    observed_at: datetime


class PurchaseImpactRead(APIModel):
    tenant_id: str
    quantity: int
    unit_cost_cents: int
    extended_cost_cents: int


class PurchaseOrderRead(APIModel):
    purchase_order_id: str
    tenant_id: str
    supplier_id: str
    sku: str
    quantity: int
    status: PurchaseOrderStatus
    expected_receipt_on: date


class PurchaseOrderList(APIModel):
    purchase_orders: list[PurchaseOrderRead]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be UTC")
    return value.astimezone(UTC)


class FixtureTerms(APIModel):
    sku: str = Field(min_length=1)
    lead_time_days: int = Field(ge=0)
    minimum_order_quantity: int = Field(ge=1)
    case_pack: int = Field(ge=1)
    unit_cost_cents: int = Field(ge=0)
    preferred: bool


class FixtureDelay(APIModel):
    delay_id: str = Field(min_length=1)
    delay_days: int = Field(ge=1)
    reason: str = Field(min_length=1)
    started_at: datetime
    active: bool

    @field_validator("started_at")
    @classmethod
    def started_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class FixtureSupplier(APIModel):
    supplier_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: SupplierStatus
    reliability_score: float = Field(ge=0, le=1)
    terms: list[FixtureTerms] = Field(min_length=1)
    delay: FixtureDelay | None = None


class FixtureStock(APIModel):
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    available: int
    reorder_point: int = Field(ge=0)
    safety_stock: int = Field(ge=0)

    @model_validator(mode="after")
    def available_matches_units(self) -> Self:
        difference = self.on_hand - self.reserved
        if self.available != difference:
            raise ValueError("available units must equal on-hand minus reserved")
        if self.available < 0:
            raise ValueError("available units must not be negative")
        return self


class FixtureStockout(APIModel):
    stockout_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime | None
    units_short: int = Field(ge=0)

    @field_validator("started_at", "ended_at")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)


class FixtureForecast(APIModel):
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    horizon_days: int = Field(ge=1)
    p10: int = Field(ge=0)
    p50: int = Field(ge=0)
    p90: int = Field(ge=0)
    trend: Trend
    quality: float = Field(ge=0, le=1)
    seasonality: SeasonalityContext
    promotion: PromotionContext

    @model_validator(mode="after")
    def quantiles_are_ordered(self) -> Self:
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError("forecast quantiles must be ordered p10 <= p50 <= p90")
        return self


class FixturePurchaseOrder(APIModel):
    purchase_order_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    status: PurchaseOrderStatus
    expected_receipt_on: date


class FixtureBudget(APIModel):
    available_cents: int = Field(ge=0)
    committed_cents: int = Field(ge=0)


class FixtureTenant(APIModel):
    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    budget: FixtureBudget
    suppliers: list[FixtureSupplier] = Field(min_length=1)
    stocks: list[FixtureStock] = Field(min_length=1)
    stockouts: list[FixtureStockout]
    forecasts: list[FixtureForecast]
    purchase_orders: list[FixturePurchaseOrder]


class TenantRead(APIModel):
    tenant_id: str
    name: str


class TenantList(APIModel):
    tenants: list[TenantRead]


class FixtureDocument(APIModel):
    observed_at: datetime
    tenants: list[FixtureTenant] = Field(min_length=2)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)
