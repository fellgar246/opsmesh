"""Run the reorder workflow through specialist tasks, then the proposal function."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AcceptedResult,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
    ContractRejection,
    EvidenceRef,
    WorkflowContext,
    accept_result,
    approvals_from_policy,
    specialist_task,
)
from opsmesh_api.operations.policy import REASON_ADEQUATE_STOCK, REASON_MISSING_EVIDENCE
from opsmesh_api.operations.quantity import ReorderMath, solve_reorder
from opsmesh_api.operations.schemas import FORECAST_HORIZON_DAYS, ReorderRequest
from opsmesh_api.workflows.models import Confidence, TaskStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import WarningItem


@dataclass
class SpecialistRun:
    task: AgentTask
    envelope: AgentResultEnvelope
    status: TaskStatus
    decision_facts: dict[str, Any] | None


@dataclass
class ReorderCoordination:
    workflow_status: WorkflowStatus
    reason: str | None
    math: ReorderMath | None
    manager: bool
    finance: bool
    runs: list[SpecialistRun] = field(default_factory=list)
    tool_calls: int = 0
    context: WorkflowContext | None = None


def _rejection_envelope(task: AgentTask, error: ContractRejection) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={},
        warnings=[WarningItem(code=error.code, message=error.message)],
        confidence=Confidence.LOW,
    )


class ReorderCoordinator:
    """Call specialists through an agent client, then solve the reorder."""

    def __init__(self, client: AgentClient) -> None:
        self._client = client

    def run(
        self, *, workflow_id: UUID, tenant_id: str, request: ReorderRequest
    ) -> ReorderCoordination:
        context = WorkflowContext(
            workflow_id=workflow_id,
            request={
                "sku": request.sku,
                "region": request.region,
                "service_level": request.service_level,
            },
            facts={},
            evidence_refs=[],
            completed_tasks=[],
            open_questions=[],
            proposals=[],
        )
        runs: list[SpecialistRun] = []
        gather: tuple[tuple[AgentType, dict[str, Any]], ...] = (
            (
                AgentType.INVENTORY,
                {"sku": request.sku, "region": request.region},
            ),
            (
                AgentType.DEMAND,
                {
                    "sku": request.sku,
                    "region": request.region,
                    "horizon_days": FORECAST_HORIZON_DAYS,
                },
            ),
            (AgentType.SUPPLIER, {"sku": request.sku}),
            (AgentType.FINANCE, {}),
        )
        for agent, parameters in gather:
            recorded = self._invoke(agent, tenant_id, parameters, context)
            runs.append(recorded)
            if recorded.decision_facts is None:
                return self._failed(context, runs, REASON_MISSING_EVIDENCE)
            _accept_into_context(context, recorded)
        inventory = context.facts[AgentType.INVENTORY.value]
        demand = context.facts[AgentType.DEMAND.value]
        supplier = context.facts[AgentType.SUPPLIER.value]
        finance = context.facts[AgentType.FINANCE.value]
        math = solve_reorder(
            service_level=request.service_level,
            p50=demand["p50"],
            p90=demand["p90"],
            available_units=inventory["available_units"],
            case_pack=supplier["case_pack"],
            minimum_order_quantity=supplier["minimum_order_quantity"],
            unit_cost_cents=supplier["unit_cost_cents"],
            supplier_status=supplier["status"],
            available_cents=finance["available_cents"],
        )
        policy = self._invoke(
            AgentType.POLICY,
            tenant_id,
            {
                "sku": request.sku,
                "extended_cost_cents": math.extended_cost_cents,
                "quantity": math.quantity,
                "unit_cost_cents": math.unit_cost_cents,
                "supplier_status": supplier["status"],
                "reason": math.reason,
                "blocked": math.blocked,
                "selected_quantile": math.selected_quantile,
                "selected_demand": math.selected_demand,
                "gross_need": math.gross_need,
            },
            context,
        )
        runs.append(policy)
        if policy.decision_facts is None:
            return self._failed(context, runs, REASON_MISSING_EVIDENCE)
        _accept_into_context(context, policy)
        manager, finance_approval = approvals_from_policy(policy.decision_facts)
        status = WorkflowStatus.COMPLETED
        if manager or finance_approval:
            status = WorkflowStatus.WAITING_APPROVAL
        arguments = _proposal_arguments(request, supplier, math, manager, finance_approval)
        if arguments is not None:
            context.proposals.append(arguments)
        return ReorderCoordination(
            workflow_status=status,
            reason=math.reason,
            math=math,
            manager=manager,
            finance=finance_approval,
            runs=runs,
            tool_calls=_tool_calls(self._client),
            context=context,
        )

    def _invoke(
        self,
        agent: AgentType,
        tenant_id: str,
        parameters: dict[str, Any],
        context: WorkflowContext,
    ) -> SpecialistRun:
        task = specialist_task(
            task_id=uuid4(),
            agent=agent,
            tenant_id=tenant_id,
            parameters=parameters,
            context=context,
        )
        try:
            payload = self._client.execute(task)
            accepted: AcceptedResult = accept_result(task, payload)
        except ContractRejection as error:
            return SpecialistRun(
                task=task,
                envelope=_rejection_envelope(task, error),
                status=TaskStatus.FAILED,
                decision_facts=None,
            )
        if not accepted.accepted:
            return SpecialistRun(
                task=task,
                envelope=accepted.envelope,
                status=TaskStatus.FAILED,
                decision_facts=None,
            )
        return SpecialistRun(
            task=task,
            envelope=accepted.envelope,
            status=TaskStatus.COMPLETED,
            decision_facts=accepted.facts,
        )

    def _failed(
        self,
        context: WorkflowContext,
        runs: list[SpecialistRun],
        reason: str,
    ) -> ReorderCoordination:
        return ReorderCoordination(
            workflow_status=WorkflowStatus.FAILED,
            reason=reason,
            math=None,
            manager=False,
            finance=False,
            runs=runs,
            tool_calls=_tool_calls(self._client),
            context=context,
        )


def _tool_calls(client: AgentClient) -> int:
    recorded = getattr(client, "tool_calls", None)
    if isinstance(recorded, int):
        return recorded
    return 0


def _accept_into_context(context: WorkflowContext, recorded: SpecialistRun) -> None:
    if recorded.decision_facts is None:
        return
    context.facts[recorded.task.agent.value] = recorded.decision_facts
    context.completed_tasks.append(recorded.task.task_id)
    for item in recorded.envelope.evidence:
        context.evidence_refs.append(
            EvidenceRef(
                type=item.type,
                source=item.source,
                source_record_id=item.source_record_id,
                observed_at=item.observed_at,
            )
        )


def _proposal_arguments(
    request: ReorderRequest,
    supplier: dict[str, Any],
    math: ReorderMath,
    manager: bool,
    finance: bool,
) -> dict[str, Any] | None:
    if math.reason is not None and math.reason != REASON_ADEQUATE_STOCK:
        return None
    return {
        "sku": request.sku,
        "region": request.region,
        "supplier_id": supplier["supplier_id"],
        "service_level": request.service_level,
        "selected_quantile": math.selected_quantile,
        "selected_demand": math.selected_demand,
        "gross_need": math.gross_need,
        "quantity": math.quantity,
        "case_pack": supplier["case_pack"],
        "minimum_order_quantity": supplier["minimum_order_quantity"],
        "unit_cost_cents": math.unit_cost_cents,
        "extended_cost_cents": math.extended_cost_cents,
        "requires_manager_approval": manager,
        "requires_finance_approval": finance,
        "reason": math.reason,
    }
