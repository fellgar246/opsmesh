"""Run one golden workflow on the local engine, or read a recorded trace."""

from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.agents.plan_schema import SupervisorPlan
from opsmesh_api.agents.planner import DeterministicPlanner, PlanningRequest
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.evaluation.schema import GoldenWorkflow, Observation, RecordedObservation
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import (
    FORECAST_HORIZON_DAYS,
    ReorderRequest,
    SupplierDelayRequest,
)
from opsmesh_api.services.models import Budget, Forecast
from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import WarningItem
from opsmesh_api.workflows.store import WorkflowStore

_AGENT_TOOLS = {
    AgentType.INVENTORY.value: "inventory.get_snapshot",
    AgentType.DEMAND.value: "forecast.get_forecast",
    AgentType.SUPPLIER.value: "supplier.get_terms",
    AgentType.FINANCE.value: "finance.get_budget",
    AgentType.POLICY.value: "policy.search",
}

_DELAY_TOOLS = (
    "supplier.get_delay",
    "supplier.list_open_pos",
    "inventory.get_snapshot",
    "forecast.get_forecast",
    "supplier.get_terms",
)


class TextPlanner:
    """Deterministic planner that also sees the request text.

    The text cannot add an agent or drop policy. A later change that obeys
    "ignore policy" fails the golden case.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._inner = DeterministicPlanner()
        self.iterations = 0
        self.model_calls = 0

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        self.iterations += 1
        self.model_calls = 0
        plan = self._inner.plan(request.model_copy(update={"text": self._text}))
        self.iterations = self._inner.iterations
        return plan


class FaultAgentClient:
    """Local specialist client that can fail one specialist closed."""

    def __init__(self, inner: StubAgentClient, fault: str | None, agent: str | None) -> None:
        self._inner = inner
        self._fault = fault
        self._agent = agent
        self._lock = Lock()
        self.tools: list[str] = []
        self.model_calls = 0

    @property
    def tool_calls(self) -> int:
        return self._inner.tool_calls

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        if self._fault == "agent_timeout" and task.agent.value == self._agent:
            return _timeout(task)
        if self._fault in {"tool_failure", "partial_failure"} and task.agent.value == self._agent:
            raise RuntimeError("specialist tool failed")
        result = self._inner.execute(task)
        if result.status == AgentResultStatus.COMPLETED:
            name = _AGENT_TOOLS.get(task.agent.value)
            if name is not None:
                with self._lock:
                    self.tools.append(name)
        return result


@dataclass
class _Prior:
    available_cents: int | None = None
    p50: int | None = None
    p90: int | None = None


def execute_case(db: Session, case: GoldenWorkflow, *, use_recorded: bool) -> Observation:
    """Execute a local case, or score a recorded trace when the full job allows it."""

    if use_recorded and case.recorded is not None:
        return _from_recorded(case.recorded)
    prior = _apply_setup(db, case)
    try:
        return _run(db, case)
    finally:
        _restore(db, case, prior)


def _run(db: Session, case: GoldenWorkflow) -> Observation:
    text = _text(case)
    planner = TextPlanner(text)
    client = FaultAgentClient(StubAgentClient(db), case.fault, case.fault_agent)
    engine = OperationalEngine(db, agent_client=client, planner=planner)
    if case.workflow_type == "supplier_delay":
        body = SupplierDelayRequest(
            requested_by=str(case.input["requested_by"]),
            supplier_id=str(case.input["supplier_id"]),
        )
        result = engine.supplier_delay(case.tenant_id, body)
        tools = list(_DELAY_TOOLS) if result.status.value == "COMPLETED" else []
        return _from_store(
            db,
            case.tenant_id,
            result.run_id,
            reason=None,
            quantity=None,
            manager=False,
            finance=False,
            estimated_cost_cents=result.cost_summary.estimated_cost_cents,
            tools=tools,
        )
    reorder_body = ReorderRequest(
        requested_by=str(case.input["requested_by"]),
        sku=str(case.input["sku"]),
        region=str(case.input["region"]),
        service_level=float(case.input["service_level"]),
    )
    reorder = engine.reorder(case.tenant_id, reorder_body)
    return _from_store(
        db,
        case.tenant_id,
        reorder.run_id,
        reason=reorder.reason,
        quantity=reorder.quantity,
        manager=reorder.requires_manager_approval,
        finance=reorder.requires_finance_approval,
        estimated_cost_cents=reorder.cost_summary.estimated_cost_cents,
        tools=list(client.tools),
    )


def _from_store(
    db: Session,
    tenant_id: str,
    run_id: Any,
    *,
    reason: str | None,
    quantity: int | None,
    manager: bool,
    finance: bool,
    estimated_cost_cents: int | None,
    tools: list[str],
) -> Observation:
    stored = WorkflowStore(db).get_run(tenant_id, run_id)
    facts: dict[str, Any] = {}
    warnings: list[str] = []
    checksums: list[str] = []
    statuses: dict[str, str] = {}
    for task in stored.tasks:
        statuses[task.agent_type] = task.status.value
        for name in task.tool_executions:
            if name.tool_name not in tools:
                tools.append(name.tool_name)
        for item in task.results:
            facts.update(_flatten(task.agent_type, item.facts))
            warnings.extend(warning.code for warning in item.warnings)
            for evidence in item.evidence:
                if evidence.citation is not None and evidence.citation.checksum not in checksums:
                    checksums.append(evidence.citation.checksum)
    proposal_roles: list[str] = []
    binding: list[str] = []
    proposal_quantity = quantity
    proposal_manager = manager
    proposal_finance = finance
    if stored.proposals:
        arguments = stored.proposals[0].arguments
        facts.update(_flatten("proposal", arguments))
        raw_binding = arguments.get("binding_constraints")
        if isinstance(raw_binding, list):
            binding = [str(item) for item in raw_binding]
        if "quantity" in arguments and isinstance(arguments["quantity"], int):
            proposal_quantity = arguments["quantity"]
        if "requires_manager_approval" in arguments:
            proposal_manager = bool(arguments["requires_manager_approval"])
        if "requires_finance_approval" in arguments:
            proposal_finance = bool(arguments["requires_finance_approval"])
        proposal_roles = [item.required_role.value for item in stored.proposals[0].approvals]
    elif not manager and not finance:
        proposal_roles = []
    if stored.plan is not None:
        agents = [task.agent for task in stored.plan.tasks]
    else:
        agents = list(statuses)
    evidence_parts = [item.source_record_id for item in stored.evidence]
    evidence_parts.append(str(facts))
    return Observation(
        status=stored.status.value,
        reason=stored.reason if stored.reason is not None else reason,
        agents=agents,
        tools=tools,
        facts=facts,
        quantity=proposal_quantity,
        requires_manager_approval=proposal_manager,
        requires_finance_approval=proposal_finance,
        approval_roles=proposal_roles,
        binding_constraints=binding,
        task_statuses=statuses,
        warning_codes=warnings,
        model_calls=stored.cost_summary.model_calls,
        tool_calls=stored.cost_summary.tool_calls,
        latency_ms=stored.cost_summary.latency_ms,
        estimated_cost_cents=stored.cost_summary.estimated_cost_cents
        if stored.cost_summary.estimated_cost_cents is not None
        else estimated_cost_cents,
        task_count=len(stored.tasks) if stored.tasks else len(agents),
        evidence_text=" ".join(evidence_parts),
        citation_checksums=checksums,
    )


def _from_recorded(recorded: RecordedObservation) -> Observation:
    return Observation(
        status=recorded.status,
        reason=recorded.reason,
        agents=list(recorded.agents),
        tools=list(recorded.tools),
        facts=dict(recorded.facts),
        quantity=recorded.quantity,
        requires_manager_approval=recorded.requires_manager_approval,
        requires_finance_approval=recorded.requires_finance_approval,
        approval_roles=list(recorded.approval_roles),
        binding_constraints=list(recorded.binding_constraints),
        task_statuses=dict(recorded.task_statuses),
        warning_codes=list(recorded.warning_codes),
        model_calls=recorded.model_calls,
        tool_calls=recorded.tool_calls,
        latency_ms=recorded.latency_ms,
        estimated_cost_cents=recorded.estimated_cost_cents,
        task_count=recorded.task_count,
        evidence_text=recorded.evidence_text,
        citation_checksums=list(recorded.citation_checksums),
    )


def _apply_setup(db: Session, case: GoldenWorkflow) -> _Prior:
    prior = _Prior()
    setup = case.setup
    if setup.available_cents is not None:
        budget = db.get(Budget, case.tenant_id)
        if budget is None:
            raise RuntimeError(f"budget missing for {case.tenant_id}")
        prior.available_cents = budget.available_cents
        budget.available_cents = setup.available_cents
    if setup.forecast_p50 is not None or setup.forecast_p90 is not None:
        forecast = db.get(
            Forecast,
            (
                case.tenant_id,
                str(case.input["sku"]),
                str(case.input["region"]),
                FORECAST_HORIZON_DAYS,
            ),
        )
        if forecast is None:
            raise RuntimeError("forecast missing for setup")
        prior.p50 = forecast.p50
        prior.p90 = forecast.p90
        if setup.forecast_p50 is not None:
            forecast.p50 = setup.forecast_p50
        if setup.forecast_p90 is not None:
            forecast.p90 = setup.forecast_p90
    db.commit()
    return prior


def _restore(db: Session, case: GoldenWorkflow, prior: _Prior) -> None:
    if prior.available_cents is not None:
        budget = db.get(Budget, case.tenant_id)
        if budget is not None:
            budget.available_cents = prior.available_cents
    if prior.p50 is not None and prior.p90 is not None:
        forecast = db.get(
            Forecast,
            (
                case.tenant_id,
                str(case.input["sku"]),
                str(case.input["region"]),
                FORECAST_HORIZON_DAYS,
            ),
        )
        if forecast is not None:
            forecast.p50 = prior.p50
            forecast.p90 = prior.p90
    if prior.available_cents is not None or prior.p50 is not None:
        db.commit()


def _flatten(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "records":
            continue
        flat[f"{prefix}.{key}"] = value
    return flat


def _text(case: GoldenWorkflow) -> str:
    raw = case.input.get("text", "")
    return raw if isinstance(raw, str) else ""


def _timeout(task: AgentTask) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={},
        warnings=[
            WarningItem(
                code="timeout",
                message="The specialist did not finish before the deadline.",
            )
        ],
        confidence=Confidence.LOW,
    )

