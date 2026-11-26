"""Argument models for read-only tools. The task tenant is authoritative."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    """Shared argument rules. A tenant value is checked against the task."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, min_length=1)


class SkuRegionArgs(ToolArgs):
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)


class ForecastArgs(SkuRegionArgs):
    horizon_days: int = Field(ge=1)


class SupplierTermsArgs(ToolArgs):
    supplier_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)


class SupplierIdArgs(ToolArgs):
    supplier_id: str = Field(min_length=1)


class PurchaseImpactArgs(ToolArgs):
    quantity: int = Field(ge=0)
    unit_cost_cents: int = Field(ge=0)


class PurchaseOrderArgs(ToolArgs):
    purchase_order_id: str = Field(min_length=1)


class PolicySearchArgs(ToolArgs):
    """Facts that select passages. The response is citations, not an approval flag."""

    extended_cost_cents: int | None = Field(default=None, ge=0)
    supplier_status: Literal["approved", "unapproved"] | None = None
    quantity: int | None = Field(default=None, ge=0)
    minimum_order_quantity: int | None = Field(default=None, ge=1)
    case_pack: int | None = Field(default=None, ge=1)
    available_cents: int | None = Field(default=None, ge=0)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    as_of: date | None = None
    max_passages: int | None = Field(default=None, ge=1)


class ApprovalRequirementArgs(ToolArgs):
    extended_cost_cents: int = Field(ge=0)


class SupplierRestrictionArgs(ToolArgs):
    supplier_status: Literal["approved", "unapproved"]
