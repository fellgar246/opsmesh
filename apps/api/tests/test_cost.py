"""Cost ceilings, early specialist skips, and the local kill switch."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.agents.fanout import MemoryJournal, PlanCoordinator
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.model_port import ModelCompletion, ModelCompletionRequest
from opsmesh_api.agents.plan_schema import validate_plan
from opsmesh_api.agents.planner import DeterministicPlanner, PlanningRequest, WorkflowKind
from opsmesh_api.agents.routing import FlaggedAgentClient, select_planner
from opsmesh_api.cost.pricing import estimate_cost_cents
from opsmesh_api.evaluation.schema import EfficiencyScores
from opsmesh_api.evaluation.score import efficiency_deltas
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.services.models import SkuStock
from opsmesh_api.settings import Settings
from opsmesh_api.tools.port import ToolCall
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.workflows.models import Confidence, TaskStatus, ToolStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import AgentTaskCreate, WarningItem, WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
REGION = "mx-central"
HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
REQUEST = ReorderRequest(
    requested_by="analyst@northwind.example",
    sku=SKU,
    region=REGION,
    service_level=0.90,
)


def test_overstock_skips_finance_and_supplier(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        _set_available(db, 400)
        result = OperationalEngine(db).reorder(NORTHWIND, REQUEST)
        stored = WorkflowStore(db).get_run(NORTHWIND, result.run_id)
    assert result.quantity == 0
    assert result.reason == "adequate_stock"
    by_agent = {task.agent_type: task for task in stored.tasks}
    for agent in ("finance", "supplier"):
        assert by_agent[agent].status == TaskStatus.SKIPPED
        assert by_agent[agent].results == []
        assert "adequate_stock" in [item.reason for item in by_agent[agent].transitions]
    assert by_agent["inventory"].status == TaskStatus.COMPLETED
    assert by_agent["demand"].status == TaskStatus.COMPLETED
    assert stored.proposals[0].arguments["quantity"] == 0


def test_equal_stock_still_runs_finance_and_supplier(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        _set_available(db, 345)
        result = OperationalEngine(db).reorder(NORTHWIND, REQUEST)
        stored = WorkflowStore(db).get_run(NORTHWIND, result.run_id)
    assert result.quantity == 0
    by_agent = {task.agent_type: task for task in stored.tasks}
    assert by_agent["finance"].status == TaskStatus.COMPLETED
    assert by_agent["supplier"].status == TaskStatus.COMPLETED


def test_daily_limit_rejects_the_sixteenth_start(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        for _ in range(15):
            _open_run(db, CONTOSO)
        allowed = client.post(
            f"/tenants/{NORTHWIND}/operations/reorder",
            json=_reorder_body(),
        )
        assert allowed.status_code == 200, allowed.text
        for _ in range(14):
            _open_run(db, NORTHWIND)
    rejected = client.post(
        f"/tenants/{NORTHWIND}/operations/reorder",
        json=_reorder_body(),
    )
    assert rejected.status_code == 429
    assert rejected.json()["detail"]["code"] == "daily_limit"


def test_output_token_ceiling_stops_further_model_calls() -> None:
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
            ],
            "dependencies": [{"task_id": "demand", "depends_on": "inventory"}],
            "reason": "Demand waits for inventory.",
        }
    )

    class _Limited:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.tool_calls = 0
            self.model_calls = 0

        def execute(self, task: AgentTask) -> AgentResultEnvelope:
            self.executed.append(task.agent.value)
            self.model_calls += 1
            return AgentResultEnvelope(
                schema_version=SCHEMA_VERSION,
                agent=task.agent,
                task_id=task.task_id,
                status=AgentResultStatus.FAILED,
                evidence=[],
                facts={},
                warnings=[
                    WarningItem(
                        code="output_token_ceiling",
                        message="The model output exceeded the output token ceiling.",
                    )
                ],
                confidence=Confidence.LOW,
            )

    client = _Limited()
    outcome = PlanCoordinator(client, MemoryJournal(plan)).run(
        workflow_id=uuid4(),
        tenant_id=NORTHWIND,
        request=REQUEST,
        plan=plan,
    )
    assert client.executed == ["inventory"]
    assert outcome.reason == "output_token_ceiling"
    assert outcome.workflow_status == WorkflowStatus.FAILED


def test_kill_switch_uses_the_local_path_for_the_next_call() -> None:
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        model_provider_enabled=True,
    )
    cloud = _Calls()
    local = _Calls()
    guarded = FlaggedAgentClient(settings, local, cloud)
    task = _inventory_task()
    guarded.execute(task)
    settings.aws_agentic_features_enabled = False
    guarded.execute(task)
    assert len(cloud.executed) == 1
    assert len(local.executed) == 1

    settings.aws_agentic_features_enabled = True
    request = PlanningRequest(workflow_type=WorkflowKind.REORDER, sku=SKU, region=REGION)
    golden = json.dumps(DeterministicPlanner().plan(request).model_dump(mode="json"))
    model = _Script([golden])
    planner = select_planner(settings, model=model)
    planner.plan(request)
    assert len(model.requests) == 1
    settings.aws_agentic_features_enabled = False
    planner.plan(request)
    assert len(model.requests) == 1
    assert planner.model_calls == 0


def test_tool_results_are_reused_only_inside_one_workflow(
    session_factory: sessionmaker[Session],
) -> None:
    arguments = {"sku": SKU, "region": REGION}
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        first_run, first_task = _tool_task(db, NORTHWIND)
        second_task = uuid4()
        WorkflowStore(db).add_task(
            NORTHWIND,
            first_run,
            AgentTaskCreate(
                id=second_task,
                agent_type=AgentType.INVENTORY.value,
                status=TaskStatus.RUNNING,
                input={"agent": "inventory"},
                started_at=datetime.now(UTC),
            ),
        )
        other_run, other_task = _tool_task(db, NORTHWIND)
        contoso_run, contoso_task = _tool_task(db, CONTOSO)
        first = registry.invoke(_tool_call(first_run, first_task, NORTHWIND, arguments))
        assert first.status == ToolStatus.SUCCEEDED
        assert registry.handler_calls == 1
        again = registry.invoke(_tool_call(first_run, second_task, NORTHWIND, arguments))
        assert again.data == first.data
        assert registry.handler_calls == 1
        registry.invoke(_tool_call(other_run, other_task, NORTHWIND, arguments))
        assert registry.handler_calls == 2
        changed = dict(arguments)
        changed["sku"] = "DEF-7"
        registry.invoke(_tool_call(first_run, first_task, NORTHWIND, changed))
        assert registry.handler_calls == 3
        registry.invoke(_tool_call(contoso_run, contoso_task, CONTOSO, arguments))
        assert registry.handler_calls == 4


def test_missing_price_is_unknown_and_a_listed_price_is_cents() -> None:
    assert (
        estimate_cost_cents(
            model_calls=0,
            input_tokens=1_000_000,
            output_tokens=0,
            model_id=None,
            unpriced=True,
        )
        == 0
    )
    assert (
        estimate_cost_cents(
            model_calls=1,
            input_tokens=1_000_000,
            output_tokens=0,
            model_id="fixture-model",
            unpriced=True,
        )
        is None
    )
    assert (
        estimate_cost_cents(
            model_calls=1,
            input_tokens=1_000_000,
            output_tokens=0,
            model_id=HAIKU,
            unpriced=False,
        )
        == 25
    )


def test_model_context_is_the_task_slice(session_factory: sessionmaker[Session]) -> None:
    model = _Script(["{}"])
    task = _inventory_task()
    with session_factory() as db:
        ModelAgentAdapter(db, model, model_id="fixture-model").execute(task)
    payload = json.loads(model.requests[0].user_prompt)
    assert set(payload) == {
        "task_id",
        "agent",
        "tenant_id",
        "parameters",
        "evidence_refs",
        "tool_results",
        "permitted_tools",
    }
    assert "transcript" not in model.requests[0].user_prompt
    assert model.requests[0].agent == AgentType.INVENTORY


def test_evaluation_deltas_cover_model_calls_and_cost() -> None:
    current = EfficiencyScores(
        task_completion=1,
        workflows=1,
        model_calls=2,
        tool_calls=3,
        latency_ms=4,
        estimated_cost_per_successful_workflow_cents=10,
    )
    previous = current.model_copy(
        update={"model_calls": 5, "estimated_cost_per_successful_workflow_cents": 40}
    )
    assert efficiency_deltas(current, previous) == (-3, -30)
    unknown = current.model_copy(update={"estimated_cost_per_successful_workflow_cents": None})
    assert efficiency_deltas(unknown, previous) == (-3, None)


class _Calls:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.tool_calls = 0
        self.model_calls = 0

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        self.executed.append(task.agent.value)
        return AgentResultEnvelope(
            schema_version=SCHEMA_VERSION,
            agent=task.agent,
            task_id=task.task_id,
            status=AgentResultStatus.COMPLETED,
            evidence=[],
            facts={"available_units": 1},
            warnings=[],
            confidence=Confidence.HIGH,
        )


class _Script:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.requests: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
        self.requests.append(request)
        text = self._texts[min(len(self.requests) - 1, len(self._texts) - 1)]
        return ModelCompletion(
            text=text,
            output_tokens=1,
            model_id=request.model_id,
            prompt_version=request.prompt_version,
        )


def _inventory_task() -> AgentTask:
    return AgentTask(
        task_id=uuid4(),
        agent=AgentType.INVENTORY,
        tenant_id=NORTHWIND,
        parameters={"sku": SKU, "region": REGION},
    )


def _set_available(db: Session, available: int) -> None:
    stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
    assert stock is not None
    stock.on_hand = available
    stock.reserved = 0
    stock.available = available
    db.commit()


def _open_run(db: Session, tenant_id: str) -> None:
    WorkflowStore(db).create_run(
        tenant_id,
        WorkflowRunCreate(
            id=uuid4(),
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status=WorkflowStatus.RUNNING,
            input={"sku": SKU},
            started_at=datetime.now(UTC),
        ),
    )


def _reorder_body() -> dict[str, object]:
    return {
        "requested_by": "analyst@northwind.example",
        "sku": SKU,
        "region": REGION,
        "service_level": 0.9,
    }


def _tool_task(db: Session, tenant_id: str) -> tuple[UUID, UUID]:
    run_id = uuid4()
    task_id = uuid4()
    store = WorkflowStore(db)
    store.create_run(
        tenant_id,
        WorkflowRunCreate(
            id=run_id,
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status=WorkflowStatus.RUNNING,
            input={"sku": SKU},
            started_at=datetime.now(UTC),
        ),
    )
    store.add_task(
        tenant_id,
        run_id,
        AgentTaskCreate(
            id=task_id,
            agent_type=AgentType.INVENTORY.value,
            status=TaskStatus.RUNNING,
            input={"agent": "inventory"},
            started_at=datetime.now(UTC),
        ),
    )
    return run_id, task_id


def _tool_call(
    run_id: UUID,
    task_id: UUID,
    tenant_id: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        tool_name="inventory.get_snapshot",
        agent=AgentType.INVENTORY,
        tenant_id=tenant_id,
        workflow_run_id=run_id,
        task_id=task_id,
        arguments=arguments,
    )
