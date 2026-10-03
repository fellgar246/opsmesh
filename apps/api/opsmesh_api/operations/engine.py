"""Run reorder, supplier-delay, and budget-plan workflows as ordinary code."""

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from opsmesh_api.agents.contracts import AgentClient
from opsmesh_api.agents.coordinator import ReorderCoordination
from opsmesh_api.agents.fanout import PlanCoordinator
from opsmesh_api.agents.plan_schema import (
    PlanTask,
    PlanValidationError,
    SupervisorPlan,
    parent_agents,
)
from opsmesh_api.agents.planner import (
    DeterministicPlanner,
    PlanningRequest,
    SupervisorPlanner,
    WorkflowKind,
)
from opsmesh_api.agents.planner import PlanCandidate as PlannerCandidate
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.operations.policy import REASON_MISSING_EVIDENCE, approval_for_total
from opsmesh_api.operations.quantity import select_demand, solve_reorder
from opsmesh_api.operations.risk import classify_stockout_risk, stockout_risk_score
from opsmesh_api.operations.schemas import (
    FORECAST_HORIZON_DAYS,
    BudgetPlanRead,
    BudgetPlanRequest,
    MitigationOption,
    PlanLine,
    ReorderRead,
    ReorderRequest,
    StockoutAssessment,
    SupplierDelayRead,
    SupplierDelayRequest,
)
from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.finance import FinanceService
from opsmesh_api.services.forecast import ForecastService
from opsmesh_api.services.inventory import InventoryService
from opsmesh_api.services.models import Tenant
from opsmesh_api.services.supplier import SupplierService
from opsmesh_api.workflows.journal import StoreJournal
from opsmesh_api.workflows.models import (
    ApprovalRole,
    ApprovalStatus,
    Confidence,
    ProposalStatus,
    RiskLevel,
    TaskStatus,
    WorkflowStatus,
)
from opsmesh_api.workflows.schemas import (
    AgentResultCreate,
    AgentTaskCreate,
    ApprovalCreate,
    CostSummary,
    EvidenceCreate,
    ProposalCreate,
    ResultEvidenceItem,
)
from opsmesh_api.workflows.store import WorkflowStore

# Delay impact has no analyst service level, so the selected forecast is P50.
_DELAY_SERVICE_LEVEL = 0.50
_APPROVAL_TTL = timedelta(days=7)


class OperationalEngine:
    """Gather simulator facts, apply policy, and persist one workflow run."""

    def __init__(
        self,
        db: Session,
        agent_client: AgentClient | None = None,
        planner: SupervisorPlanner | None = None,
    ) -> None:
        self._db = db
        self._inventory = InventoryService(db)
        self._forecast = ForecastService(db)
        self._supplier = SupplierService(db)
        self._finance = FinanceService(db)
        self._store = WorkflowStore(db)
        self._agent_client = agent_client
        self._planner = planner if planner is not None else DeterministicPlanner()

    def reorder(self, tenant_id: str, body: ReorderRequest) -> ReorderRead:
        self._require_tenant(tenant_id)
        started = datetime.now(UTC)
        timer = perf_counter()
        run_id = uuid4()
        try:
            plan = self._planner.plan(
                PlanningRequest(
                    workflow_type=WorkflowKind.REORDER,
                    sku=body.sku,
                    region=body.region,
                    horizon_days=FORECAST_HORIZON_DAYS,
                )
            )
        except PlanValidationError as error:
            self._persist_rejected_plan(
                tenant_id=tenant_id,
                run_id=run_id,
                workflow_type="reorder",
                requested_by=body.requested_by,
                workflow_input={
                    "sku": body.sku,
                    "region": body.region,
                    "service_level": body.service_level,
                },
                started=started,
                latency_ms=_latency(timer),
            )
            return ReorderRead(
                run_id=run_id,
                workflow_type="reorder",
                status=WorkflowStatus.FAILED,
                reason=error.code,
                cost_summary=_planner_cost(self._planner, _latency(timer)),
            )
        client = self._agent_client or StubAgentClient(self._db)
        task_ids = self._seed_reorder(tenant_id, run_id, body, started, plan)
        journal = StoreJournal(self._store, tenant_id, run_id, plan, task_ids)
        outcome = PlanCoordinator(client, journal).run(
            workflow_id=run_id,
            tenant_id=tenant_id,
            request=body,
            plan=plan,
        )
        latency_ms = _latency(timer)
        risk = RiskLevel.HIGH
        if outcome.math is not None and outcome.context is not None:
            inventory = outcome.context.facts["inventory"]
            supplier = outcome.context.facts["supplier"]
            score = stockout_risk_score(
                inventory["available_units"],
                supplier["lead_time_days"],
                outcome.math.selected_demand,
                FORECAST_HORIZON_DAYS,
            )
            risk = classify_stockout_risk(score) if outcome.math.quantity > 0 else RiskLevel.LOW
        arguments = None
        if outcome.context is not None and outcome.context.proposals:
            arguments = outcome.context.proposals[0]
        self._finish_reorder(
            tenant_id=tenant_id,
            run_id=run_id,
            outcome=outcome,
            latency_ms=latency_ms,
            proposal_arguments=arguments,
            risk_level=risk,
        )
        math = outcome.math
        return ReorderRead(
            run_id=run_id,
            workflow_type="reorder",
            status=outcome.workflow_status,
            reason=outcome.reason,
            selected_quantile=None if math is None else math.selected_quantile,
            selected_demand=None if math is None else math.selected_demand,
            gross_need=None if math is None else math.gross_need,
            quantity=None if math is None else math.quantity,
            unit_cost_cents=None if math is None else math.unit_cost_cents,
            extended_cost_cents=None if math is None else math.extended_cost_cents,
            requires_manager_approval=outcome.manager,
            requires_finance_approval=outcome.finance,
            proposal_arguments=arguments,
            cost_summary=_cost(
                outcome.tool_calls,
                latency_ms,
                outcome.model_calls + self._planner.model_calls,
                self._planner.iterations,
            ),
        )

    def _seed_reorder(
        self,
        tenant_id: str,
        run_id: UUID,
        body: ReorderRequest,
        started: datetime,
        plan: SupervisorPlan,
    ) -> dict[str, UUID]:
        """Open the run and its tasks before any specialist is started."""

        from opsmesh_api.workflows.schemas import WorkflowRunCreate

        self._store.create_run(
            tenant_id,
            WorkflowRunCreate(
                id=run_id,
                workflow_type="reorder",
                requested_by=body.requested_by,
                status=WorkflowStatus.RUNNING,
                input={
                    "sku": body.sku,
                    "region": body.region,
                    "service_level": body.service_level,
                },
                started_at=started,
                cost_summary=_planner_cost(self._planner, 0),
                plan=plan,
            ),
        )
        ids = {task.id: uuid4() for task in plan.tasks}
        for task in _topological_tasks(plan):
            depends_on = [
                ids[edge.depends_on] for edge in plan.dependencies if edge.task_id == task.id
            ]
            self._store.add_task(
                tenant_id,
                run_id,
                AgentTaskCreate(
                    id=ids[task.id],
                    agent_type=task.agent,
                    depends_on=depends_on,
                    status=TaskStatus.PENDING,
                    input={
                        "plan_task_id": task.id,
                        "agent": task.agent,
                        "parameters": task.input,
                    },
                ),
            )
        return ids

    def _finish_reorder(
        self,
        *,
        tenant_id: str,
        run_id: UUID,
        outcome: ReorderCoordination,
        latency_ms: int,
        proposal_arguments: dict[str, Any] | None,
        risk_level: RiskLevel,
    ) -> None:
        finished = datetime.now(UTC)
        summary = _cost(
            outcome.tool_calls,
            latency_ms,
            outcome.model_calls + self._planner.model_calls,
            self._planner.iterations,
        )
        self._store.update_run(
            tenant_id,
            run_id,
            status=outcome.workflow_status,
            reason=outcome.reason,
            write_reason=True,
            finished_at=finished,
            cost_summary=summary.model_dump(mode="json"),
        )
        if proposal_arguments is None:
            return
        proposal_id = uuid4()
        proposal_status = (
            ProposalStatus.PENDING_APPROVAL
            if outcome.manager or outcome.finance
            else ProposalStatus.DRAFT
        )
        self._store.add_proposal(
            tenant_id,
            run_id,
            ProposalCreate(
                id=proposal_id,
                proposal_type="reorder",
                arguments=proposal_arguments,
                estimated_impact={"extended_cost_cents": _impact_cents(proposal_arguments)},
                risk_level=risk_level,
                status=proposal_status,
                created_at=finished,
            ),
        )
        expires = finished + _APPROVAL_TTL
        if outcome.manager:
            self._store.add_approval(
                tenant_id,
                run_id,
                proposal_id,
                ApprovalCreate(
                    id=uuid4(),
                    arguments=proposal_arguments,
                    required_role=ApprovalRole.PROCUREMENT_MANAGER,
                    status=ApprovalStatus.PENDING,
                    expires_at=expires,
                ),
            )
        if outcome.finance:
            self._store.add_approval(
                tenant_id,
                run_id,
                proposal_id,
                ApprovalCreate(
                    id=uuid4(),
                    arguments=proposal_arguments,
                    required_role=ApprovalRole.FINANCE_APPROVER,
                    status=ApprovalStatus.PENDING,
                    expires_at=expires,
                ),
            )

    def supplier_delay(self, tenant_id: str, body: SupplierDelayRequest) -> SupplierDelayRead:
        self._require_tenant(tenant_id)
        started = datetime.now(UTC)
        timer = perf_counter()
        try:
            plan = self._planner.plan(
                PlanningRequest(
                    workflow_type=WorkflowKind.SUPPLIER_DELAY,
                    supplier_id=body.supplier_id,
                    horizon_days=FORECAST_HORIZON_DAYS,
                )
            )
        except PlanValidationError:
            run_id = uuid4()
            self._persist_rejected_plan(
                tenant_id=tenant_id,
                run_id=run_id,
                workflow_type="supplier_delay",
                requested_by=body.requested_by,
                workflow_input={"supplier_id": body.supplier_id},
                started=started,
                latency_ms=_latency(timer),
            )
            return SupplierDelayRead(
                run_id=run_id,
                workflow_type="supplier_delay",
                status=WorkflowStatus.FAILED,
                supplier_id=body.supplier_id,
                delay_id="",
                delay_days=0,
                affected_orders=[],
                affected_skus=[],
                stockout_risk=[],
                mitigations=[],
                cost_summary=_planner_cost(self._planner, _latency(timer)),
            )
        calls = 1
        delay = self._supplier.get_delay(tenant_id, body.supplier_id)
        calls += 1
        orders = self._supplier.list_open_purchase_orders(tenant_id, body.supplier_id)
        facts: list[_Fact] = [
            _from_model("supplier", "supplier_delay", delay.delay_id, delay),
        ]
        assessments: list[StockoutAssessment] = []
        skus: list[str] = []
        order_ids = [order.purchase_order_id for order in orders.purchase_orders]
        for order in orders.purchase_orders:
            facts.append(
                _from_model(
                    "procurement",
                    "purchase_order",
                    order.purchase_order_id,
                    order,
                )
            )
            if order.sku in skus:
                continue
            skus.append(order.sku)
            calls += 1
            snapshots = self._inventory.list_snapshots_for_sku(tenant_id, order.sku)
            if not snapshots:
                return self._failed_delay(
                    tenant_id, body, delay.delay_id, started, timer, calls, facts, plan
                )
            for snapshot in snapshots:
                facts.append(
                    _from_model(
                        "inventory",
                        "inventory_snapshot",
                        f"{snapshot.sku}:{snapshot.region}",
                        snapshot,
                    )
                )
                try:
                    calls += 1
                    forecast = self._forecast.get_forecast(
                        tenant_id, snapshot.sku, snapshot.region, FORECAST_HORIZON_DAYS
                    )
                except NotFoundError:
                    return self._failed_delay(
                        tenant_id, body, delay.delay_id, started, timer, calls, facts, plan
                    )
                facts.append(
                    _from_model(
                        "demand",
                        "forecast",
                        f"{snapshot.sku}:{snapshot.region}:{FORECAST_HORIZON_DAYS}",
                        forecast,
                    )
                )
                calls += 1
                terms = self._supplier.get_terms(tenant_id, body.supplier_id, snapshot.sku)
                # The delay pushes receipt out, so lead time includes delay days.
                exposed_lead_time = terms.lead_time_days + delay.delay_days
                _, selected = select_demand(_DELAY_SERVICE_LEVEL, forecast.p50, forecast.p90)
                score = stockout_risk_score(
                    snapshot.available_units,
                    exposed_lead_time,
                    selected,
                    forecast.horizon_days,
                )
                assessments.append(
                    StockoutAssessment(
                        sku=snapshot.sku,
                        region=snapshot.region,
                        score=score,
                        level=classify_stockout_risk(score),
                    )
                )
        mitigations = _mitigations(order_ids, assessments)
        arguments: dict[str, Any] = {
            "supplier_id": body.supplier_id,
            "delay_id": delay.delay_id,
            "delay_days": delay.delay_days,
            "affected_orders": order_ids,
            "affected_skus": skus,
            "stockout_risk": [item.model_dump(mode="json") for item in assessments],
            "mitigations": [item.model_dump(mode="json") for item in mitigations],
        }
        facts.append(_computed("policy", "delay_impact", delay.delay_id, arguments, started))
        run_id = self._persist(
            tenant_id=tenant_id,
            workflow_type="supplier_delay",
            requested_by=body.requested_by,
            status=WorkflowStatus.COMPLETED,
            workflow_input={"supplier_id": body.supplier_id},
            started=started,
            latency_ms=_latency(timer),
            tool_calls=calls,
            facts=facts,
            proposal_arguments=arguments,
            proposal_type="supplier_delay",
            risk_level=_worst(assessments),
            requires_manager=False,
            requires_finance=False,
            plan=plan,
        )
        return SupplierDelayRead(
            run_id=run_id,
            workflow_type="supplier_delay",
            status=WorkflowStatus.COMPLETED,
            supplier_id=body.supplier_id,
            delay_id=delay.delay_id,
            delay_days=delay.delay_days,
            affected_orders=order_ids,
            affected_skus=skus,
            stockout_risk=assessments,
            mitigations=mitigations,
            proposal_arguments=arguments,
            cost_summary=_planner_cost(self._planner, _latency(timer), calls),
        )

    def budget_plan(self, tenant_id: str, body: BudgetPlanRequest) -> BudgetPlanRead:
        self._require_tenant(tenant_id)
        started = datetime.now(UTC)
        timer = perf_counter()
        try:
            plan = self._planner.plan(
                PlanningRequest(
                    workflow_type=WorkflowKind.BUDGET_PLAN,
                    horizon_days=FORECAST_HORIZON_DAYS,
                    candidates=[
                        PlannerCandidate(sku=item.sku, region=item.region)
                        for item in body.candidates
                    ],
                )
            )
        except PlanValidationError:
            run_id = uuid4()
            self._persist_rejected_plan(
                tenant_id=tenant_id,
                run_id=run_id,
                workflow_type="budget_plan",
                requested_by=body.requested_by,
                workflow_input={
                    "candidates": [item.model_dump(mode="json") for item in body.candidates]
                },
                started=started,
                latency_ms=_latency(timer),
            )
            return BudgetPlanRead(
                run_id=run_id,
                workflow_type="budget_plan",
                status=WorkflowStatus.FAILED,
                lines=[],
                cost_summary=_planner_cost(self._planner, _latency(timer)),
            )
        calls = 1
        budget = self._finance.get_budget(tenant_id)
        facts: list[_Fact] = [_from_model("finance", "budget", tenant_id, budget)]
        prepared: list[tuple[int, PlanLine, dict[str, Any]]] = []
        try:
            for candidate in body.candidates:
                calls += 1
                snapshot = self._inventory.get_snapshot(tenant_id, candidate.sku, candidate.region)
                facts.append(
                    _from_model(
                        "inventory",
                        "inventory_snapshot",
                        f"{candidate.sku}:{candidate.region}",
                        snapshot,
                    )
                )
                calls += 1
                forecast = self._forecast.get_forecast(
                    tenant_id, candidate.sku, candidate.region, FORECAST_HORIZON_DAYS
                )
                facts.append(
                    _from_model(
                        "demand",
                        "forecast",
                        f"{candidate.sku}:{candidate.region}:{FORECAST_HORIZON_DAYS}",
                        forecast,
                    )
                )
                calls += 1
                preferred = self._supplier.get_preferred(tenant_id, candidate.sku)
                calls += 1
                terms = self._supplier.get_terms(tenant_id, preferred.supplier_id, candidate.sku)
                facts.append(
                    _from_model(
                        "supplier",
                        "supplier_terms",
                        f"{terms.supplier_id}:{candidate.sku}",
                        terms,
                    )
                )
                _, selected = select_demand(candidate.service_level, forecast.p50, forecast.p90)
                score = stockout_risk_score(
                    snapshot.available_units,
                    terms.lead_time_days,
                    selected,
                    forecast.horizon_days,
                )
                prepared.append(
                    (
                        score,
                        PlanLine(
                            sku=candidate.sku,
                            region=candidate.region,
                            supplier_id=terms.supplier_id,
                            funded=False,
                            stockout_risk_score=score,
                            selected_demand=selected,
                        ),
                        {
                            "service_level": candidate.service_level,
                            "p50": forecast.p50,
                            "p90": forecast.p90,
                            "available_units": snapshot.available_units,
                            "case_pack": terms.case_pack,
                            "minimum_order_quantity": terms.minimum_order_quantity,
                            "unit_cost_cents": terms.unit_cost_cents,
                            "supplier_status": terms.status.value,
                        },
                    )
                )
        except NotFoundError:
            return self._failed_plan(tenant_id, body, started, timer, calls, facts, plan)

        prepared.sort(key=lambda item: (-item[0], item[1].sku, item[1].region))
        remaining = budget.available_cents
        lines: list[PlanLine] = []
        any_manager = False
        any_finance = False
        for score, line, raw in prepared:
            math = solve_reorder(
                service_level=raw["service_level"],
                p50=raw["p50"],
                p90=raw["p90"],
                available_units=raw["available_units"],
                case_pack=raw["case_pack"],
                minimum_order_quantity=raw["minimum_order_quantity"],
                unit_cost_cents=raw["unit_cost_cents"],
                supplier_status=raw["supplier_status"],
                available_cents=remaining,
            )
            manager = False
            finance = False
            funded = math.feasible and math.quantity > 0
            if funded:
                manager, finance = approval_for_total(math.extended_cost_cents)
                remaining -= math.extended_cost_cents
                calls += 1
                self._finance.estimate(tenant_id, math.quantity, math.unit_cost_cents)
                any_manager = any_manager or manager
                any_finance = any_finance or finance
            lines.append(
                line.model_copy(
                    update={
                        "funded": funded,
                        "reason": None if funded else math.reason,
                        "quantity": math.quantity if funded else 0,
                        "unit_cost_cents": math.unit_cost_cents,
                        "extended_cost_cents": math.extended_cost_cents if funded else 0,
                        "requires_manager_approval": manager,
                        "requires_finance_approval": finance,
                        "stockout_risk_score": score,
                        "selected_demand": math.selected_demand,
                    }
                )
            )
        arguments = {"lines": [item.model_dump(mode="json") for item in lines]}
        facts.append(_computed("policy", "budget_plan", tenant_id, arguments, started))
        status = WorkflowStatus.COMPLETED
        if any_manager or any_finance:
            status = WorkflowStatus.WAITING_APPROVAL
        run_id = self._persist(
            tenant_id=tenant_id,
            workflow_type="budget_plan",
            requested_by=body.requested_by,
            status=status,
            workflow_input={
                "candidates": [item.model_dump(mode="json") for item in body.candidates]
            },
            started=started,
            latency_ms=_latency(timer),
            tool_calls=calls,
            facts=facts,
            proposal_arguments=arguments,
            proposal_type="budget_plan",
            risk_level=_worst_score(lines),
            requires_manager=any_manager,
            requires_finance=any_finance,
            plan=plan,
        )
        return BudgetPlanRead(
            run_id=run_id,
            workflow_type="budget_plan",
            status=status,
            lines=lines,
            proposal_arguments=arguments,
            cost_summary=_planner_cost(self._planner, _latency(timer), calls),
        )

    def _failed_delay(
        self,
        tenant_id: str,
        body: SupplierDelayRequest,
        delay_id: str,
        started: datetime,
        timer: float,
        calls: int,
        facts: list["_Fact"],
        plan: SupervisorPlan,
    ) -> SupplierDelayRead:
        facts.append(
            _computed(
                "policy",
                "delay_impact",
                delay_id,
                {"reason": REASON_MISSING_EVIDENCE},
                started,
            )
        )
        run_id = self._persist(
            tenant_id=tenant_id,
            workflow_type="supplier_delay",
            requested_by=body.requested_by,
            status=WorkflowStatus.FAILED,
            workflow_input={"supplier_id": body.supplier_id},
            started=started,
            latency_ms=_latency(timer),
            tool_calls=calls,
            facts=facts,
            proposal_arguments=None,
            proposal_type="supplier_delay",
            risk_level=RiskLevel.HIGH,
            requires_manager=False,
            requires_finance=False,
            plan=plan,
        )
        return SupplierDelayRead(
            run_id=run_id,
            workflow_type="supplier_delay",
            status=WorkflowStatus.FAILED,
            supplier_id=body.supplier_id,
            delay_id=delay_id,
            delay_days=0,
            affected_orders=[],
            affected_skus=[],
            stockout_risk=[],
            mitigations=[],
            cost_summary=_planner_cost(self._planner, _latency(timer), calls),
        )

    def _failed_plan(
        self,
        tenant_id: str,
        body: BudgetPlanRequest,
        started: datetime,
        timer: float,
        calls: int,
        facts: list["_Fact"],
        plan: SupervisorPlan,
    ) -> BudgetPlanRead:
        facts.append(
            _computed(
                "policy",
                "budget_plan",
                tenant_id,
                {"reason": REASON_MISSING_EVIDENCE},
                started,
            )
        )
        run_id = self._persist(
            tenant_id=tenant_id,
            workflow_type="budget_plan",
            requested_by=body.requested_by,
            status=WorkflowStatus.FAILED,
            workflow_input={
                "candidates": [item.model_dump(mode="json") for item in body.candidates]
            },
            started=started,
            latency_ms=_latency(timer),
            tool_calls=calls,
            facts=facts,
            proposal_arguments=None,
            proposal_type="budget_plan",
            risk_level=RiskLevel.HIGH,
            requires_manager=False,
            requires_finance=False,
            plan=plan,
        )
        return BudgetPlanRead(
            run_id=run_id,
            workflow_type="budget_plan",
            status=WorkflowStatus.FAILED,
            lines=[],
            cost_summary=_planner_cost(self._planner, _latency(timer), calls),
        )

    def _persist_rejected_plan(
        self,
        *,
        tenant_id: str,
        run_id: UUID,
        workflow_type: str,
        requested_by: str,
        workflow_input: dict[str, Any],
        started: datetime,
        latency_ms: int,
    ) -> None:
        """Store a failed run with no tasks when the plan never validates."""

        from opsmesh_api.workflows.schemas import WorkflowRunCreate

        self._store.create_run(
            tenant_id,
            WorkflowRunCreate(
                id=run_id,
                workflow_type=workflow_type,
                requested_by=requested_by,
                status=WorkflowStatus.FAILED,
                input=workflow_input,
                started_at=started,
                finished_at=datetime.now(UTC),
                cost_summary=_planner_cost(self._planner, latency_ms),
            ),
        )

    def _require_tenant(self, tenant_id: str) -> None:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()

    def _persist(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        requested_by: str,
        status: WorkflowStatus,
        workflow_input: dict[str, Any],
        started: datetime,
        latency_ms: int,
        tool_calls: int,
        facts: list["_Fact"],
        proposal_arguments: dict[str, Any] | None,
        proposal_type: str,
        risk_level: RiskLevel,
        requires_manager: bool,
        requires_finance: bool,
        plan: SupervisorPlan | None = None,
    ) -> UUID:
        from opsmesh_api.workflows.schemas import WorkflowRunCreate

        run_id = uuid4()
        finished = datetime.now(UTC)
        summary = _planner_cost(self._planner, latency_ms, tool_calls)
        self._store.create_run(
            tenant_id,
            WorkflowRunCreate(
                id=run_id,
                workflow_type=workflow_type,
                requested_by=requested_by,
                status=status,
                input=workflow_input,
                started_at=started,
                finished_at=finished,
                cost_summary=summary,
                plan=plan,
            ),
        )
        grouped: dict[str, list[_Fact]] = {}
        for fact in facts:
            grouped.setdefault(fact.agent_type, []).append(fact)
        agent_ids = {agent_type: uuid4() for agent_type in grouped}
        for agent_type in _ordered_names(list(grouped), plan):
            group = grouped[agent_type]
            task_id = agent_ids[agent_type]
            depends_on = [
                agent_ids[parent]
                for parent in (parent_agents(plan, agent_type) if plan is not None else [])
                if parent in agent_ids
            ]
            self._store.add_task(
                tenant_id,
                run_id,
                AgentTaskCreate(
                    id=task_id,
                    agent_type=agent_type,
                    depends_on=depends_on,
                    status=TaskStatus.COMPLETED,
                    input=workflow_input,
                    started_at=started,
                    finished_at=finished,
                ),
            )
            evidence_items = [
                ResultEvidenceItem(
                    type=fact.evidence_type,
                    source=fact.agent_type,
                    source_record_id=fact.source_record_id,
                    observed_at=fact.observed_at,
                )
                for fact in group
            ]
            self._store.add_result(
                tenant_id,
                run_id,
                task_id,
                AgentResultCreate(
                    id=uuid4(),
                    schema_version="1",
                    facts={"records": [fact.payload for fact in group]},
                    evidence=evidence_items,
                    warnings=[],
                    confidence=Confidence.HIGH,
                    model_id=None,
                    prompt_version=None,
                ),
            )
        for fact in facts:
            if fact.agent_type == "policy":
                continue
            self._store.add_evidence(
                tenant_id,
                run_id,
                EvidenceCreate(
                    id=uuid4(),
                    type=fact.evidence_type,
                    source=fact.agent_type,
                    source_record_id=fact.source_record_id,
                    observed_at=fact.observed_at,
                    checksum=fact.checksum,
                ),
            )
        if proposal_arguments is not None:
            proposal_id = uuid4()
            proposal_status = (
                ProposalStatus.PENDING_APPROVAL
                if requires_manager or requires_finance
                else ProposalStatus.DRAFT
            )
            self._store.add_proposal(
                tenant_id,
                run_id,
                ProposalCreate(
                    id=proposal_id,
                    proposal_type=proposal_type,
                    arguments=proposal_arguments,
                    estimated_impact={"extended_cost_cents": _impact_cents(proposal_arguments)},
                    risk_level=risk_level,
                    status=proposal_status,
                    created_at=finished,
                ),
            )
            expires = finished + _APPROVAL_TTL
            if requires_manager:
                self._store.add_approval(
                    tenant_id,
                    run_id,
                    proposal_id,
                    ApprovalCreate(
                        id=uuid4(),
                        arguments=proposal_arguments,
                        required_role=ApprovalRole.PROCUREMENT_MANAGER,
                        status=ApprovalStatus.PENDING,
                        expires_at=expires,
                    ),
                )
            if requires_finance:
                self._store.add_approval(
                    tenant_id,
                    run_id,
                    proposal_id,
                    ApprovalCreate(
                        id=uuid4(),
                        arguments=proposal_arguments,
                        required_role=ApprovalRole.FINANCE_APPROVER,
                        status=ApprovalStatus.PENDING,
                        expires_at=expires,
                    ),
                )
        return run_id


class _Fact:
    def __init__(
        self,
        agent_type: str,
        evidence_type: str,
        source_record_id: str,
        payload: dict[str, Any],
        observed_at: datetime,
        checksum: str,
    ) -> None:
        self.agent_type = agent_type
        self.evidence_type = evidence_type
        self.source_record_id = source_record_id
        self.payload = payload
        self.observed_at = observed_at
        self.checksum = checksum


def _from_model(agent_type: str, evidence_type: str, source_record_id: str, model: Any) -> _Fact:
    from opsmesh_api.workflows.hashing import canonical_json_hash

    payload = model.model_dump(mode="json")
    observed = payload.get("observed_at") or payload.get("started_at")
    if isinstance(observed, str):
        stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    else:
        stamp = datetime.now(UTC)
    return _Fact(
        agent_type,
        evidence_type,
        source_record_id,
        payload,
        stamp,
        canonical_json_hash(payload),
    )


def _computed(
    agent_type: str,
    evidence_type: str,
    source_record_id: str,
    payload: dict[str, Any],
    observed_at: datetime,
) -> _Fact:
    from opsmesh_api.workflows.hashing import canonical_json_hash

    return _Fact(
        agent_type,
        evidence_type,
        source_record_id,
        payload,
        observed_at,
        canonical_json_hash(payload),
    )


def _cost(
    tool_calls: int,
    latency_ms: int,
    model_calls: int = 0,
    supervisor_iterations: int = 0,
) -> CostSummary:
    return CostSummary(
        model_calls=model_calls,
        tool_calls=tool_calls,
        latency_ms=latency_ms,
        estimated_cost_cents=0,
        supervisor_iterations=supervisor_iterations,
    )


def _planner_cost(
    planner: SupervisorPlanner,
    latency_ms: int,
    tool_calls: int = 0,
) -> CostSummary:
    return _cost(tool_calls, latency_ms, planner.model_calls, planner.iterations)


def _ordered_names(names: list[str], plan: SupervisorPlan | None) -> list[str]:
    """Insert parents before the tasks that name them."""

    if plan is None:
        return list(names)
    present = set(names)
    parents = {
        name: [parent for parent in parent_agents(plan, name) if parent in present]
        for name in names
    }
    ordered: list[str] = []
    remaining = list(dict.fromkeys(names))
    while remaining:
        ready = [name for name in remaining if all(parent in ordered for parent in parents[name])]
        if not ready:
            return list(dict.fromkeys(names))
        ordered.extend(ready)
        remaining = [name for name in remaining if name not in ordered]
    return ordered


def _topological_tasks(plan: SupervisorPlan) -> list[PlanTask]:
    """Parents are inserted before the tasks that name them."""

    deps: dict[str, list[str]] = {task.id: [] for task in plan.tasks}
    for edge in plan.dependencies:
        deps[edge.task_id].append(edge.depends_on)
    pending = [task.id for task in plan.tasks]
    ordered: list[str] = []
    while pending:
        ready = [
            task_id for task_id in pending if all(parent in ordered for parent in deps[task_id])
        ]
        if not ready:
            break
        ordered.extend(ready)
        pending = [task_id for task_id in pending if task_id not in ordered]
    by_id = {task.id: task for task in plan.tasks}
    return [by_id[task_id] for task_id in ordered]


def _latency(timer: float) -> int:
    return max(0, int((perf_counter() - timer) * 1000))


def _impact_cents(arguments: dict[str, Any]) -> int:
    direct = arguments.get("extended_cost_cents")
    if isinstance(direct, int):
        return direct
    lines = arguments.get("lines")
    if isinstance(lines, list):
        return sum(int(line.get("extended_cost_cents", 0)) for line in lines)
    return 0


def _mitigations(
    order_ids: list[str],
    assessments: list[StockoutAssessment],
) -> list[MitigationOption]:
    options = [
        MitigationOption(
            code="do_not_place_order",
            reason="delay_impact_is_advisory",
        )
    ]
    if order_ids:
        options.append(
            MitigationOption(
                code="expedite_open_order",
                reason="open_order_covers_affected_sku",
            )
        )
    if any(item.level == RiskLevel.HIGH for item in assessments):
        options.append(
            MitigationOption(
                code="protect_remaining_stock",
                reason="lead_time_demand_exceeds_available_units",
            )
        )
    return options


def _worst(assessments: list[StockoutAssessment]) -> RiskLevel:
    if any(item.level == RiskLevel.HIGH for item in assessments):
        return RiskLevel.HIGH
    if any(item.level == RiskLevel.MEDIUM for item in assessments):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _worst_score(lines: list[PlanLine]) -> RiskLevel:
    if not lines:
        return RiskLevel.LOW
    return classify_stockout_risk(max(line.stockout_risk_score for line in lines))
