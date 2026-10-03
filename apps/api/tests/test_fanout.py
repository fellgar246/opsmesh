"""Parallel execution of a validated plan, with failures, retries, and deadlines."""

from datetime import UTC, datetime
from threading import Barrier
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.agents.fanout import MemoryJournal, PlanCoordinator
from opsmesh_api.agents.plan_schema import SupervisorPlan, validate_plan
from opsmesh_api.agents.planner import DeterministicPlanner, PlanningRequest, WorkflowKind
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.agents.transitions import IllegalTransition, assert_transition
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.workflows.errors import PersistenceRejected
from opsmesh_api.workflows.models import Confidence, TaskStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import (
    AgentTaskCreate,
    CostSummary,
    WorkflowRunCreate,
)
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
SKU = "ABC-42"
REGION = "mx-central"


def _request() -> ReorderRequest:
    return ReorderRequest(
        requested_by="analyst@northwind.example",
        sku=SKU,
        region=REGION,
        service_level=0.90,
    )


def _plan() -> SupervisorPlan:
    return DeterministicPlanner().plan(
        PlanningRequest(workflow_type=WorkflowKind.REORDER, sku=SKU, region=REGION)
    )


def _ok(task: AgentTask, facts: dict[str, object]) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.COMPLETED,
        evidence=[],
        facts=facts,
        warnings=[],
        confidence=Confidence.HIGH,
    )


def _failed(task: AgentTask) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={"lead_time_days": 7, "unit_cost_cents": 1},
        warnings=[],
        confidence=Confidence.LOW,
    )


def _facts(agent: AgentType) -> dict[str, object]:
    if agent == AgentType.INVENTORY:
        return {
            "sku": SKU,
            "region": REGION,
            "available_units": 128,
            "reserved_units": 0,
            "on_hand_units": 128,
        }
    if agent == AgentType.DEMAND:
        return {
            "sku": SKU,
            "region": REGION,
            "horizon_days": 30,
            "p50": 310,
            "p90": 345,
        }
    if agent == AgentType.SUPPLIER:
        return {
            "sku": SKU,
            "supplier_id": "andina-supply",
            "status": "approved",
            "lead_time_days": 14,
            "minimum_order_quantity": 100,
            "case_pack": 50,
            "unit_cost_cents": 3700,
            "reliability_score": 0.9,
        }
    if agent == AgentType.FINANCE:
        return {"available_cents": 5_000_000, "committed_cents": 0}
    return {
        "reason": None,
        "blocked": False,
        "selected_quantile": "p90",
        "selected_demand": 345,
        "gross_need": 217,
        "quantity": 250,
        "unit_cost_cents": 3700,
        "extended_cost_cents": 925_000,
        "requires_manager_approval": True,
        "requires_finance_approval": False,
        "explanation": "The purchase total requires manager approval.",
    }


class _CountingClient:
    def __init__(self) -> None:
        self.tool_calls = 0
        self.model_calls = 0
        self.executed: list[str] = []
        self.write_calls: list[str] = []

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        self.executed.append(task.agent.value)
        self.tool_calls += 1
        self.model_calls += 1
        return _ok(task, _facts(task.agent))

    def write(self) -> None:
        self.write_calls.append("procurement.create")


def test_independent_tasks_overlap_and_finance_waits(
    session_factory: sessionmaker[Session],
) -> None:
    barrier = Barrier(3)
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}

    class _BarrierClient:
        def __init__(self, inner: StubAgentClient) -> None:
            self._inner = inner
            self.tool_calls = 0
            self.model_calls = 0

        def execute(self, task: AgentTask) -> AgentResultEnvelope:
            starts[task.agent.value] = monotonic()
            if task.agent.value in {"inventory", "demand", "supplier"}:
                barrier.wait(timeout=2)
            result = self._inner.execute(task)
            finishes[task.agent.value] = monotonic()
            self.tool_calls = self._inner.tool_calls
            return result

    plan = _plan()
    journal = MemoryJournal(plan)
    with session_factory() as db:
        outcome = PlanCoordinator(_BarrierClient(StubAgentClient(db)), journal).run(
            workflow_id=uuid4(),
            tenant_id=NORTHWIND,
            request=_request(),
            plan=plan,
        )
    overlapped = ("inventory", "demand", "supplier")
    assert max(starts[name] for name in overlapped) <= min(finishes[name] for name in overlapped)
    assert starts["finance"] >= max(finishes[name] for name in overlapped)
    assert any(
        snapshot["inventory"] == TaskStatus.RUNNING
        and snapshot["demand"] == TaskStatus.RUNNING
        and snapshot["supplier"] == TaskStatus.RUNNING
        and snapshot["finance"] in {TaskStatus.PENDING, TaskStatus.READY}
        and TaskStatus.COMPLETED
        not in {snapshot["inventory"], snapshot["demand"], snapshot["supplier"]}
        for snapshot in journal.history
    )
    assert outcome.workflow_status == WorkflowStatus.WAITING_APPROVAL
    assert outcome.math is not None
    assert outcome.math.quantity == 250
    assert {task.status for task in journal.tasks()} == {TaskStatus.COMPLETED}


def test_joined_completions_start_the_downstream_task_once() -> None:
    plan = validate_plan(
        {
            "tasks": [
                {
                    "id": "inventory",
                    "agent": "inventory",
                    "input": {"sku": SKU, "region": REGION},
                    "read_only": True,
                },
                {
                    "id": "demand",
                    "agent": "demand",
                    "input": {"sku": SKU, "region": REGION, "horizon_days": 30},
                    "read_only": True,
                },
                {"id": "finance", "agent": "finance", "input": {}, "read_only": True},
            ],
            "dependencies": [
                {"task_id": "finance", "depends_on": "inventory"},
                {"task_id": "finance", "depends_on": "demand"},
            ],
            "reason": "Finance waits until inventory and demand have both finished.",
        }
    )
    barrier = Barrier(2)
    client = _CountingClient()
    original = client.execute

    def _execute(task: AgentTask) -> AgentResultEnvelope:
        if task.agent.value in {"inventory", "demand"}:
            barrier.wait(timeout=2)
        return original(task)

    client.execute = _execute  # type: ignore[method-assign]
    journal = MemoryJournal(plan)
    PlanCoordinator(client, journal).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    assert client.executed.count("finance") == 1


def test_supplier_failure_skips_dependents_and_survives_restart(
    session_factory: sessionmaker[Session],
) -> None:
    class _FailSupplier(StubAgentClient):
        def execute(self, task: AgentTask) -> AgentResultEnvelope:
            if task.agent == AgentType.SUPPLIER:
                return _failed(task)
            return super().execute(task)

    with session_factory() as db:
        engine = OperationalEngine(db, agent_client=_FailSupplier(db))
        result = engine.reorder(NORTHWIND, _request())
        assert result.status == WorkflowStatus.FAILED
        assert result.reason == "missing_evidence"
        assert result.proposal_arguments is None
        run_id = result.run_id

    with session_factory() as db:
        stored = WorkflowStore(db).get_run(NORTHWIND, run_id)
    by_agent = {task.agent_type: task for task in stored.tasks}
    assert by_agent["supplier"].status == TaskStatus.FAILED
    assert by_agent["supplier"].retry_count == 2
    assert by_agent["supplier"].results[0].facts == {}
    assert by_agent["inventory"].status == TaskStatus.COMPLETED
    assert by_agent["demand"].status == TaskStatus.COMPLETED
    assert by_agent["finance"].status == TaskStatus.SKIPPED
    assert by_agent["policy"].status == TaskStatus.SKIPPED
    assert stored.proposals == []
    assert stored.reason == "missing_evidence"
    assert "lead_time_days" not in str(stored.model_dump(mode="json"))
    reasons = [item.reason for item in by_agent["finance"].transitions]
    assert "dependency_not_completed" in reasons


def test_skipped_dependency_does_not_start_the_downstream_task() -> None:
    client = _CountingClient()
    original = client.execute

    def _execute(task: AgentTask) -> AgentResultEnvelope:
        if task.agent == AgentType.SUPPLIER:
            return _failed(task)
        return original(task)

    client.execute = _execute  # type: ignore[method-assign]
    plan = _plan()
    journal = MemoryJournal(plan)
    PlanCoordinator(client, journal, max_retries=0).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    assert "finance" not in client.executed
    assert "policy" not in client.executed
    statuses = {task.plan_id: task.status for task in journal.tasks()}
    assert statuses["finance"] == TaskStatus.SKIPPED
    assert statuses["policy"] == TaskStatus.SKIPPED


def test_a_failed_task_retries_twice_then_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        inner = StubAgentClient(db)

        class _Flaky:
            def __init__(self) -> None:
                self.tool_calls = 0
                self.model_calls = 0
                self.attempts = 0

            def execute(self, task: AgentTask) -> AgentResultEnvelope:
                if task.agent == AgentType.SUPPLIER:
                    self.attempts += 1
                    if self.attempts <= 2:
                        return _failed(task)
                result = inner.execute(task)
                self.tool_calls = inner.tool_calls
                return result

        client = _Flaky()
        plan = _plan()
        journal = MemoryJournal(plan)
        outcome = PlanCoordinator(client, journal).run(
            workflow_id=uuid4(),
            tenant_id=NORTHWIND,
            request=_request(),
            plan=plan,
        )
    supplier = next(task for task in journal.tasks() if task.plan_id == "supplier")
    assert client.attempts == 3
    assert supplier.retry_count == 2
    assert supplier.status == TaskStatus.COMPLETED
    assert outcome.math is not None
    assert outcome.math.quantity == 250


def test_task_timeout_does_not_commit_facts_or_retry_past_two() -> None:
    class _Slow:
        def __init__(self) -> None:
            self.tool_calls = 0
            self.model_calls = 0
            self.starts = 0

        def execute(self, task: AgentTask) -> AgentResultEnvelope:
            if task.agent == AgentType.INVENTORY:
                self.starts += 1
                sleep(0.2)
            self.tool_calls += 1
            return _ok(task, _facts(task.agent))

    client = _Slow()
    plan = _plan()
    journal = MemoryJournal(plan)
    outcome = PlanCoordinator(client, journal, task_timeout_seconds=0.05).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    sleep(0.45)
    inventory = next(task for task in journal.tasks() if task.plan_id == "inventory")
    assert inventory.status == TaskStatus.FAILED
    assert inventory.retry_count == 2
    assert client.starts == 3
    assert journal.committed[inventory.id] == {}
    assert outcome.workflow_status == WorkflowStatus.FAILED
    assert outcome.reason == "missing_evidence"
    assert outcome.context is not None
    assert outcome.context.proposals == []


def test_workflow_deadline_fails_closed_without_a_write() -> None:
    client = _CountingClient()
    original = client.execute

    def _execute(task: AgentTask) -> AgentResultEnvelope:
        if task.agent.value in {"inventory", "demand", "supplier"}:
            sleep(0.2)
        return original(task)

    client.execute = _execute  # type: ignore[method-assign]
    plan = _plan()
    journal = MemoryJournal(plan)
    outcome = PlanCoordinator(
        client,
        journal,
        deadline_seconds=0.05,
        task_timeout_seconds=5,
    ).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    sleep(0.45)
    assert outcome.workflow_status == WorkflowStatus.FAILED
    assert outcome.reason == "workflow_timeout"
    assert "finance" not in client.executed
    assert client.write_calls == []
    assert outcome.context is not None
    assert outcome.context.proposals == []
    for task in journal.tasks():
        if task.plan_id in {"inventory", "demand", "supplier"}:
            assert journal.committed.get(task.id, {}) == {}


def test_call_ceilings_block_the_next_task() -> None:
    plan = validate_plan(
        {
            "tasks": [
                {
                    "id": "inventory",
                    "agent": "inventory",
                    "input": {"sku": SKU, "region": REGION},
                    "read_only": True,
                },
                {
                    "id": "demand",
                    "agent": "demand",
                    "input": {"sku": SKU, "region": REGION, "horizon_days": 30},
                    "read_only": True,
                },
                {"id": "supplier", "agent": "supplier", "input": {"sku": SKU}, "read_only": True},
            ],
            "dependencies": [
                {"task_id": "demand", "depends_on": "inventory"},
                {"task_id": "supplier", "depends_on": "demand"},
            ],
            "reason": "Each specialist waits for the previous one.",
        }
    )
    client = _CountingClient()
    journal = MemoryJournal(plan)
    outcome = PlanCoordinator(client, journal, max_tool_calls=2, max_model_calls=20).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    assert client.executed == ["inventory", "demand"]
    assert client.tool_calls == 2
    assert outcome.reason == "tool_call_limit"
    assert outcome.workflow_status == WorkflowStatus.FAILED
    supplier = next(task for task in journal.tasks() if task.plan_id == "supplier")
    assert supplier.status == TaskStatus.SKIPPED

    model_client = _CountingClient()
    model_journal = MemoryJournal(plan)
    model_outcome = PlanCoordinator(
        model_client,
        model_journal,
        max_tool_calls=30,
        max_model_calls=1,
    ).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    assert model_client.executed == ["inventory"]
    assert model_outcome.reason == "model_call_limit"


def test_cancellation_stops_ready_work_and_does_not_start_new_tasks() -> None:
    plan = _plan()
    holder: dict[str, PlanCoordinator] = {}

    class _CancelJournal(MemoryJournal):
        def transition(
            self,
            task_id: UUID,
            status: TaskStatus,
            reason: str,
            at: datetime,
        ) -> None:
            super().transition(task_id, status, reason, at)
            coordinator = holder.get("coordinator")
            if status == TaskStatus.READY and coordinator is not None:
                coordinator.cancel()

    journal = _CancelJournal(plan)
    client = _CountingClient()
    coordinator = PlanCoordinator(client, journal)
    holder["coordinator"] = coordinator
    outcome = coordinator.run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=_request(),
        plan=plan,
    )
    assert outcome.workflow_status == WorkflowStatus.CANCELLED
    assert client.executed == []
    statuses = {task.plan_id: task.status for task in journal.tasks()}
    assert statuses["inventory"] == TaskStatus.CANCELLED
    assert statuses["finance"] == TaskStatus.PENDING
    assert statuses["policy"] == TaskStatus.PENDING


def test_pending_cannot_jump_to_completed(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IllegalTransition):
        assert_transition(TaskStatus.PENDING, TaskStatus.COMPLETED)
    with session_factory() as db:
        store = WorkflowStore(db)
        run_id = uuid4()
        task_id = uuid4()
        store.create_run(
            NORTHWIND,
            WorkflowRunCreate(
                id=run_id,
                workflow_type="reorder",
                requested_by="analyst@northwind.example",
                status=WorkflowStatus.RUNNING,
                input={"sku": SKU},
                started_at=datetime.now(UTC),
                cost_summary=CostSummary(),
            ),
        )
        store.add_task(
            NORTHWIND,
            run_id,
            AgentTaskCreate(
                id=task_id,
                agent_type="inventory",
                status=TaskStatus.PENDING,
                input={"sku": SKU},
            ),
        )
        with pytest.raises(PersistenceRejected):
            store.transition_task(
                NORTHWIND,
                run_id,
                task_id,
                status=TaskStatus.COMPLETED,
                reason="completed",
                at=datetime.now(UTC),
            )
        with pytest.raises(PersistenceRejected):
            store.set_retry_count(NORTHWIND, run_id, task_id, 3)
