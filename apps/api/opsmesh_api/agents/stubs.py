"""In-process specialists. They call the simulator or the policy service, never a model."""

from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.services.errors import NotFoundError, StockConflictError
from opsmesh_api.services.finance import FinanceService
from opsmesh_api.services.forecast import ForecastService
from opsmesh_api.services.inventory import InventoryService
from opsmesh_api.services.supplier import SupplierService
from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import ResultEvidenceItem, WarningItem


class _InventoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    region: str


class _DemandParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    region: str
    horizon_days: int


class _SupplierParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str


class _PolicyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    extended_cost_cents: int
    quantity: int
    unit_cost_cents: int
    supplier_status: str
    reason: str | None
    blocked: bool
    selected_quantile: str
    selected_demand: int
    gross_need: int


def _warning(code: str, message: str) -> WarningItem:
    return WarningItem(code=code, message=message)


def _failed(task: AgentTask, code: str, message: str) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={},
        warnings=[_warning(code, message)],
        confidence=Confidence.LOW,
    )


def _completed(
    task: AgentTask,
    facts: dict[str, Any],
    evidence: list[ResultEvidenceItem],
) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.COMPLETED,
        evidence=evidence,
        facts=facts,
        warnings=[],
        confidence=Confidence.HIGH,
    )


def _evidence(
    evidence_type: str,
    source: str,
    source_record_id: str,
    observed_at: datetime,
) -> ResultEvidenceItem:
    return ResultEvidenceItem(
        type=evidence_type,
        source=source,
        source_record_id=source_record_id,
        observed_at=observed_at,
    )


def _policy_explanation(
    *,
    reason: str | None,
    requires_manager: bool,
    requires_finance: bool,
) -> str:
    if reason == "supplier_not_approved":
        return "The supplier is not approved, so the purchase is blocked."
    if reason == "insufficient_budget":
        return "The available budget cannot fund the minimum order."
    if reason == "adequate_stock":
        return "Available stock already covers the selected demand."
    if reason == "missing_evidence":
        return "A required simulator record is missing, so no purchase is proposed."
    if requires_manager and requires_finance:
        return "The purchase total requires manager and finance approval."
    if requires_manager:
        return "The purchase total requires manager approval."
    if requires_finance:
        return "The purchase total requires finance approval."
    return "The purchase total does not require additional approval."


class StubAgentClient:
    """Local specialist client. A later transport can implement the same port."""

    def __init__(self, db: Session, *, timeout_seconds: float | None = None) -> None:
        self._inventory = InventoryService(db)
        self._forecast = ForecastService(db)
        self._supplier = SupplierService(db)
        self._finance = FinanceService(db)
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self.tool_calls = 0

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        with self._lock:
            return self._execute(task)

    def _execute(self, task: AgentTask) -> AgentResultEnvelope:
        if self._timeout_seconds is not None and self._timeout_seconds <= 0:
            return _failed(
                task,
                "timeout",
                "The specialist did not finish before the deadline.",
            )
        started = perf_counter()
        try:
            result = self._dispatch(task)
        except NotFoundError:
            result = _failed(
                task,
                "missing_evidence",
                "The specialist could not find the requested record.",
            )
        except StockConflictError:
            result = _failed(
                task,
                "stock_conflict",
                "Reserved units exceed on-hand units.",
            )
        except ValidationError:
            result = _failed(
                task,
                "invalid_input",
                "The specialist task parameters are invalid.",
            )
        if self._timeout_seconds is not None and perf_counter() - started > self._timeout_seconds:
            return _failed(
                task,
                "timeout",
                "The specialist did not finish before the deadline.",
            )
        return result

    def _dispatch(self, task: AgentTask) -> AgentResultEnvelope:
        if task.agent == AgentType.INVENTORY:
            return self._inventory_stub(task)
        if task.agent == AgentType.DEMAND:
            return self._demand_stub(task)
        if task.agent == AgentType.SUPPLIER:
            return self._supplier_stub(task)
        if task.agent == AgentType.FINANCE:
            return self._finance_stub(task)
        if task.agent == AgentType.POLICY:
            return self._policy_stub(task)
        return _failed(task, "unknown_agent", "The specialist is not available.")

    def _inventory_stub(self, task: AgentTask) -> AgentResultEnvelope:
        params = _InventoryParams.model_validate(task.parameters)
        self.tool_calls += 1
        snapshot = self._inventory.get_snapshot(task.tenant_id, params.sku, params.region)
        facts = {
            "sku": snapshot.sku,
            "region": snapshot.region,
            "available_units": snapshot.available_units,
            "reserved_units": snapshot.reserved_units,
            "on_hand_units": snapshot.on_hand_units,
        }
        return _completed(
            task,
            facts,
            [
                _evidence(
                    "inventory_snapshot",
                    AgentType.INVENTORY.value,
                    f"{snapshot.sku}:{snapshot.region}",
                    snapshot.observed_at,
                )
            ],
        )

    def _demand_stub(self, task: AgentTask) -> AgentResultEnvelope:
        params = _DemandParams.model_validate(task.parameters)
        self.tool_calls += 1
        forecast = self._forecast.get_forecast(
            task.tenant_id, params.sku, params.region, params.horizon_days
        )
        facts = {
            "sku": forecast.sku,
            "region": forecast.region,
            "horizon_days": forecast.horizon_days,
            "p50": forecast.p50,
            "p90": forecast.p90,
        }
        return _completed(
            task,
            facts,
            [
                _evidence(
                    "forecast",
                    AgentType.DEMAND.value,
                    f"{forecast.sku}:{forecast.region}:{forecast.horizon_days}",
                    forecast.observed_at,
                )
            ],
        )

    def _supplier_stub(self, task: AgentTask) -> AgentResultEnvelope:
        params = _SupplierParams.model_validate(task.parameters)
        self.tool_calls += 1
        preferred = self._supplier.get_preferred(task.tenant_id, params.sku)
        self.tool_calls += 1
        terms = self._supplier.get_terms(task.tenant_id, preferred.supplier_id, params.sku)
        facts = {
            "sku": terms.sku,
            "supplier_id": terms.supplier_id,
            "status": terms.status.value,
            "lead_time_days": terms.lead_time_days,
            "minimum_order_quantity": terms.minimum_order_quantity,
            "case_pack": terms.case_pack,
            "unit_cost_cents": terms.unit_cost_cents,
            "reliability_score": terms.reliability_score,
        }
        return _completed(
            task,
            facts,
            [
                _evidence(
                    "supplier_terms",
                    AgentType.SUPPLIER.value,
                    f"{terms.supplier_id}:{terms.sku}",
                    datetime.now(UTC),
                )
            ],
        )

    def _finance_stub(self, task: AgentTask) -> AgentResultEnvelope:
        if task.parameters:
            return _failed(task, "invalid_input", "The specialist task parameters are invalid.")
        self.tool_calls += 1
        budget = self._finance.get_budget(task.tenant_id)
        facts = {
            "available_cents": budget.available_cents,
            "committed_cents": budget.committed_cents,
        }
        return _completed(
            task,
            facts,
            [
                _evidence(
                    "budget",
                    AgentType.FINANCE.value,
                    task.tenant_id,
                    budget.observed_at,
                )
            ],
        )

    def _policy_stub(self, task: AgentTask) -> AgentResultEnvelope:
        params = _PolicyParams.model_validate(task.parameters)
        requires_manager = False
        requires_finance = False
        if params.reason is None and params.quantity > 0:
            requires_manager, requires_finance = approval_for_total(params.extended_cost_cents)
        explanation = _policy_explanation(
            reason=params.reason,
            requires_manager=requires_manager,
            requires_finance=requires_finance,
        )
        facts = {
            "reason": params.reason,
            "blocked": params.blocked,
            "selected_quantile": params.selected_quantile,
            "selected_demand": params.selected_demand,
            "gross_need": params.gross_need,
            "quantity": params.quantity,
            "unit_cost_cents": params.unit_cost_cents,
            "extended_cost_cents": params.extended_cost_cents,
            "requires_manager_approval": requires_manager,
            "requires_finance_approval": requires_finance,
            "explanation": explanation,
        }
        return _completed(
            task,
            facts,
            [
                _evidence(
                    "policy_decision",
                    AgentType.POLICY.value,
                    params.sku,
                    datetime.now(UTC),
                )
            ],
        )
