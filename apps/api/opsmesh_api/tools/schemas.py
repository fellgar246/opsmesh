"""Argument models for tools. The task tenant is authoritative."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh_api.workflows.hashing import canonical_json_hash

CREATE_DRAFT_TOOL = "procurement.create_draft_purchase_order"
SUBMIT_FOR_APPROVAL_TOOL = "procurement.submit_purchase_order_for_approval"
WRITE_TOOL_NAMES = frozenset({CREATE_DRAFT_TOOL, SUBMIT_FOR_APPROVAL_TOOL})


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


class DraftPurchaseOrderArgs(ToolArgs):
    """Commercial arguments that must match a feasible proposal."""

    proposal_id: UUID
    sku: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit_cost_cents: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SubmitPurchaseOrderArgs(ToolArgs):
    """Identify one local draft and the key for a safe replay."""

    purchase_order_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


def validated_arguments_hash(model: BaseModel) -> str:
    """Hash of the validated arguments. Null optional fields are omitted."""

    return canonical_json_hash(model.model_dump(mode="json", exclude_none=True))
