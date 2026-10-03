"""Inputs and decisions for deterministic operational workflows."""

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from opsmesh_api.services.schemas import APIModel
from opsmesh_api.workflows.models import RiskLevel, WorkflowStatus
from opsmesh_api.workflows.schemas import CostSummary

WorkflowTypeName = Literal["reorder", "supplier_delay", "budget_plan"]
FORECAST_HORIZON_DAYS = 30


class ReorderRequest(APIModel):
    requested_by: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    service_level: float = Field(ge=0, le=1)


class SupplierDelayRequest(APIModel):
    requested_by: str = Field(min_length=1, max_length=200)
    supplier_id: str = Field(min_length=1)


class PlanCandidate(APIModel):
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    service_level: float = Field(ge=0, le=1)


class BudgetPlanRequest(APIModel):
    requested_by: str = Field(min_length=1, max_length=200)
    candidates: list[PlanCandidate] = Field(min_length=1)

    @model_validator(mode="after")
    def candidates_are_unique(self) -> Self:
        keys = [(item.sku, item.region) for item in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("candidates must be unique")
        return self


class MitigationOption(APIModel):
    code: str
    reason: str


class StockoutAssessment(APIModel):
    sku: str
    region: str
    score: int
    level: RiskLevel


class PlanLine(APIModel):
    sku: str
    region: str
    supplier_id: str | None = None
    funded: bool
    reason: str | None = None
    quantity: int = 0
    unit_cost_cents: int = 0
    extended_cost_cents: int = 0
    requires_manager_approval: bool = False
    requires_finance_approval: bool = False
    stockout_risk_score: int = 0
    selected_demand: int | None = None


class ReorderRead(APIModel):
    run_id: UUID
    workflow_type: WorkflowTypeName
    status: WorkflowStatus
    reason: str | None
    selected_quantile: str | None = None
    selected_demand: int | None = None
    gross_need: int | None = None
    quantity: int | None = None
    unit_cost_cents: int | None = None
    extended_cost_cents: int | None = None
    requires_manager_approval: bool = False
    requires_finance_approval: bool = False
    proposal_arguments: dict[str, Any] | None = None
    explanation: str | None = None
    cost_summary: CostSummary


class SupplierDelayRead(APIModel):
    run_id: UUID
    workflow_type: WorkflowTypeName
    status: WorkflowStatus
    supplier_id: str
    delay_id: str
    delay_days: int
    affected_orders: list[str]
    affected_skus: list[str]
    stockout_risk: list[StockoutAssessment]
    mitigations: list[MitigationOption]
    proposal_arguments: dict[str, Any] | None = None
    cost_summary: CostSummary


class BudgetPlanRead(APIModel):
    run_id: UUID
    workflow_type: WorkflowTypeName
    status: WorkflowStatus
    lines: list[PlanLine]
    proposal_arguments: dict[str, Any] | None = None
    cost_summary: CostSummary
