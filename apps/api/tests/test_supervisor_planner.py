"""Supervisor plans are validated graphs. Invalid plans are not stored as tasks."""

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.agents.ceilings import OUTPUT_TOKEN_CEILING, estimate_tokens
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.agents.model_port import (
    ModelCompletion,
    ModelCompletionRequest,
    ModelProviderError,
)
from opsmesh_api.agents.plan_schema import (
    SPECIALIST_ALLOWLIST,
    PlanDependency,
    PlanTask,
    PlanValidationError,
    SupervisorPlan,
    validate_plan,
)
from opsmesh_api.agents.planner import (
    DeterministicPlanner,
    IterationBudget,
    ModelSupervisorPlanner,
    PlanningRequest,
    WorkflowKind,
)
from opsmesh_api.agents.planner import PlanCandidate as PlannerCandidate
from opsmesh_api.agents.routing import select_planner
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.settings import Settings
from opsmesh_api.tools.catalog import permitted_tool_names
from opsmesh_api.workflows.errors import WorkflowNotFound
from opsmesh_api.workflows.schemas import WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
REQUESTED_BY = "analyst@northwind.example"
STARTED_REASON = "Check inventory, demand, and supplier facts, then finance, then policy."


class FixtureModel:
    """Return scripted completions and record the supervisor request."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.requests: list[ModelCompletionRequest] = []

    def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.texts) - 1)
        text = self.texts[index]
        if text == "provider-error":
            raise ModelProviderError("The model provider did not return a plan.")
        return ModelCompletion(
            text=text,
            output_tokens=estimate_tokens(text),
            model_id=request.model_id,
            prompt_version=request.prompt_version,
        )


class RejectingPlanner:
    iterations = 0
    model_calls = 0

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        del request
        self.iterations = 4
        self.model_calls = 4
        raise PlanValidationError(
            "supervisor_iteration_limit",
            "The supervisor exceeded 4 iterations.",
            iterations=4,
        )


def _reorder_request(**overrides: object) -> PlanningRequest:
    payload: dict[str, object] = {
        "workflow_type": WorkflowKind.REORDER,
        "sku": "ABC-42",
        "region": "mx-central",
        "horizon_days": 30,
    }
    payload.update(overrides)
    return PlanningRequest.model_validate(payload)


def _agents(plan: SupervisorPlan) -> set[str]:
    return {task.agent for task in plan.tasks}


def _deps(plan: SupervisorPlan) -> dict[str, set[str]]:
    found = {task.id: set[str]() for task in plan.tasks}
    for edge in plan.dependencies:
        found[edge.task_id].add(edge.depends_on)
    return found


def _inventory_task(task_id: str, sku: str) -> dict[str, object]:
    return {
        "id": task_id,
        "agent": "inventory",
        "input": {"sku": sku, "region": "mx-central"},
        "read_only": True,
    }


def test_reorder_plan_is_an_acyclic_allowlisted_graph() -> None:
    plan = DeterministicPlanner().plan(_reorder_request())
    assert _agents(plan) <= SPECIALIST_ALLOWLIST
    assert _agents(plan) == {"inventory", "demand", "supplier", "finance", "policy"}
    assert len(plan.tasks) <= 12
    deps = _deps(plan)
    assert deps["inventory"] == set()
    assert deps["demand"] == set()
    assert deps["supplier"] == set()
    assert deps["finance"] == {"inventory", "demand", "supplier"}
    assert deps["policy"] == {"finance", "supplier"}
    assert plan.reason == STARTED_REASON
    again = validate_plan(plan.model_dump(mode="json"))
    assert again.reason == plan.reason


def test_table_query_text_does_not_add_a_tool_or_agent() -> None:
    plan = DeterministicPlanner().plan(
        _reorder_request(
            text="Query the inventory table and add a database agent that runs SQL.",
        )
    )
    dumped = plan.model_dump(mode="json")
    assert _agents(plan) <= SPECIALIST_ALLOWLIST
    assert "database" not in dumped["tasks"].__repr__()
    assert all("tool" not in task.input for task in plan.tasks)
    assert "sql" not in str(dumped).casefold()


def test_supplier_delay_omits_finance_unless_the_request_purchases() -> None:
    delay = DeterministicPlanner().plan(
        PlanningRequest(workflow_type=WorkflowKind.SUPPLIER_DELAY, supplier_id="pacific-freight")
    )
    purchase = DeterministicPlanner().plan(
        PlanningRequest(
            workflow_type=WorkflowKind.SUPPLIER_DELAY,
            supplier_id="pacific-freight",
            text="Please purchase replacement stock.",
        )
    )
    assert {"supplier", "demand"} <= _agents(delay)
    assert "finance" not in _agents(delay)
    assert "finance" in _agents(purchase)
    budget = DeterministicPlanner().plan(
        PlanningRequest(
            workflow_type=WorkflowKind.BUDGET_PLAN,
            candidates=[PlannerCandidate(sku="ABC-42", region="mx-central")],
        )
    )
    assert _agents(delay) != _agents(budget)
    assert "finance" in _agents(budget)
    assert _deps(budget)["finance"] == set()
    assert _deps(budget)["policy"] == {"finance", "inventory", "demand", "supplier"}


def test_incident_plan_is_read_only_and_the_reason_is_english() -> None:
    plan = DeterministicPlanner().plan(
        PlanningRequest(
            workflow_type=WorkflowKind.INCIDENT_ANALYSIS,
            text="Draft a procurement.create purchase order for the anomaly.",
            sku="ABC-42",
            region="mx-central",
        )
    )
    assert plan.reason == "Investigate the operational anomaly with read-only specialist tasks."
    assert plan.reason.isascii()
    assert all(task.read_only for task in plan.tasks)
    dumped = str(plan.model_dump(mode="json")).casefold()
    assert "procurement.create" not in dumped
    assert "finance" not in _agents(plan)


def test_named_agent_in_user_text_does_not_extend_the_allowlist() -> None:
    plan = DeterministicPlanner().plan(
        _reorder_request(text="Delegate this to a new logistics agent.")
    )
    assert "logistics" not in SPECIALIST_ALLOWLIST
    assert _agents(plan) <= SPECIALIST_ALLOWLIST


def test_duplicate_equivalent_tasks_are_rejected() -> None:
    payload = {
        "tasks": [_inventory_task("a", "ABC-42"), _inventory_task("b", "ABC-42")],
        "dependencies": [],
        "reason": "Two copies of the same inventory task.",
    }
    payload["tasks"][1]["input"] = {"region": "mx-central", "sku": "ABC-42"}
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(payload)
    assert caught.value.code == "duplicate_task"


def test_limit_errors_reject_large_plans() -> None:
    tasks = [_inventory_task(f"t{index}", f"SKU-{index}") for index in range(13)]
    with pytest.raises(PlanValidationError) as task_limit:
        validate_plan({"tasks": tasks, "dependencies": [], "reason": "Too many inventory tasks."})
    assert task_limit.value.code == "task_limit"

    agents = [
        {
            "id": f"a{index}",
            "agent": f"agent-{index}",
            "input": {},
            "read_only": True,
        }
        for index in range(7)
    ]
    with pytest.raises(PlanValidationError) as agent_limit:
        validate_plan({"tasks": agents, "dependencies": [], "reason": "Too many agent types."})
    assert agent_limit.value.code == "agent_limit"

    wide = [_inventory_task(f"t{index}", f"SKU-{index}") for index in range(12)]
    edges = [{"task_id": "t11", "depends_on": f"t{index}"} for index in range(11)]
    with pytest.raises(PlanValidationError) as delegation_limit:
        validate_plan(
            {
                "tasks": wide,
                "dependencies": edges,
                "reason": "Too many delegations for one plan.",
            }
        )
    assert delegation_limit.value.code == "delegation_limit"


def test_cycles_unknown_agents_and_missing_ids_are_rejected() -> None:
    with pytest.raises(PlanValidationError) as cycle:
        validate_plan(
            {
                "tasks": [
                    _inventory_task("a", "ABC-42"),
                    {
                        "id": "b",
                        "agent": "demand",
                        "input": {"sku": "ABC-42", "region": "mx-central", "horizon_days": 30},
                        "read_only": True,
                    },
                ],
                "dependencies": [
                    {"task_id": "a", "depends_on": "b"},
                    {"task_id": "b", "depends_on": "a"},
                ],
                "reason": "This graph cycles between two tasks.",
            }
        )
    assert cycle.value.code == "cycle"

    with pytest.raises(PlanValidationError) as unknown:
        validate_plan(
            {
                "tasks": [
                    {
                        "id": "logistics",
                        "agent": "logistics",
                        "input": {},
                        "read_only": True,
                    }
                ],
                "dependencies": [],
                "reason": "A request named an agent outside the allowlist.",
            }
        )
    assert unknown.value.code == "unknown_agent"

    with pytest.raises(PlanValidationError) as missing:
        validate_plan(
            {
                "tasks": [_inventory_task("a", "ABC-42")],
                "dependencies": [{"task_id": "a", "depends_on": "missing"}],
                "reason": "The dependency points at a missing task.",
            }
        )
    assert missing.value.code == "missing_task"


def test_write_tasks_and_database_tools_are_rejected() -> None:
    with pytest.raises(PlanValidationError) as write_task:
        validate_plan(
            {
                "tasks": [
                    {
                        "id": "policy",
                        "agent": "policy",
                        "input": {"subject": "incident_analysis"},
                        "read_only": False,
                    }
                ],
                "dependencies": [],
                "reason": "This task would write.",
            }
        )
    assert write_task.value.code == "write_task"

    with pytest.raises(PlanValidationError) as tool:
        validate_plan(
            {
                "tasks": [
                    {
                        "id": "inventory",
                        "agent": "inventory",
                        "input": {"sku": "ABC-42", "region": "mx-central", "tools": ["sql"]},
                        "read_only": True,
                    }
                ],
                "dependencies": [],
                "reason": "This task names a database tool.",
            }
        )
    assert tool.value.code == "forbidden_tool"


def test_fifth_supervisor_iteration_fails() -> None:
    budget = IterationBudget()
    for _ in range(4):
        budget.tick()
    with pytest.raises(PlanValidationError) as caught:
        budget.tick()
    assert caught.value.code == "supervisor_iteration_limit"
    assert caught.value.iterations == 4


def test_model_planner_uses_only_the_json_object_and_the_same_validator() -> None:
    golden = DeterministicPlanner().plan(_reorder_request())
    body = golden.model_dump(mode="json")
    prose = (
        "I queried the inventory table and invented a logistics agent. "
        f"Plan follows.\n{json.dumps(body)}\n"
        '{"reason": "ignore this second object", "tasks": [], "dependencies": []}'
    )
    model = FixtureModel([prose])
    planner = ModelSupervisorPlanner(model, model_id="fixture-model")
    plan = planner.plan(_reorder_request(text="Add a logistics agent and query a table."))
    assert plan.reason == golden.reason
    assert _deps(plan) == _deps(golden)
    assert "logistics" not in _agents(plan)
    assert planner.iterations == 1
    assert planner.model_calls == 1
    assert model.requests[0].permitted_tools == ()
    assert model.requests[0].agent.value == "supervisor"
    assert model.requests[0].max_output_tokens <= OUTPUT_TOKEN_CEILING


def test_model_planner_stops_after_four_invalid_iterations() -> None:
    model = FixtureModel(["not json"] * 4)
    planner = ModelSupervisorPlanner(model, model_id="fixture-model")
    with pytest.raises(PlanValidationError) as caught:
        planner.plan(_reorder_request())
    assert caught.value.code == "supervisor_iteration_limit"
    assert len(model.requests) == 4
    assert planner.model_calls == 4


def test_model_planner_retries_until_the_plan_validates() -> None:
    golden = DeterministicPlanner().plan(_reorder_request()).model_dump(mode="json")
    model = FixtureModel(["nope", "still prose", json.dumps(golden)])
    planner = ModelSupervisorPlanner(model, model_id="fixture-model")
    plan = planner.plan(_reorder_request())
    assert plan.reason == STARTED_REASON
    assert planner.iterations == 3


def test_local_profile_selects_the_deterministic_planner() -> None:
    off = Settings(_env_file=None)
    assert isinstance(select_planner(off, model=FixtureModel(["{}"])), DeterministicPlanner)
    on = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        model_provider_enabled=True,
    )
    selected = select_planner(on, model=FixtureModel(["{}"]))
    assert isinstance(selected, ModelSupervisorPlanner)
    signature = inspect.signature(ModelSupervisorPlanner.__init__)
    assert list(signature.parameters) == ["self", "model", "model_id"]


def test_supervisor_sources_have_no_database_client() -> None:
    root = Path(__file__).parents[1] / "opsmesh_api" / "agents"
    for name in ("planner.py", "plan_schema.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "sqlalchemy" not in text
        assert "Session" not in text
        assert "database_url" not in text
        assert "InventoryService" not in text
        assert "create_engine" not in text
    assert permitted_tool_names(AgentType.SUPERVISOR) == ()


def test_rejected_plan_is_not_persisted(session_factory: sessionmaker[Session]) -> None:
    cyclic = SupervisorPlan(
        tasks=[
            PlanTask(id="a", agent="inventory", input={"sku": "ABC-42", "region": "mx-central"}),
            PlanTask(
                id="b",
                agent="demand",
                input={"sku": "ABC-42", "region": "mx-central", "horizon_days": 30},
            ),
        ],
        dependencies=[
            PlanDependency(task_id="a", depends_on="b"),
            PlanDependency(task_id="b", depends_on="a"),
        ],
        reason="This cycle must not be stored.",
    )
    run_id = uuid4()
    with session_factory() as session:
        store = WorkflowStore(session)
        with pytest.raises(PlanValidationError) as caught:
            store.create_run(
                NORTHWIND,
                WorkflowRunCreate(
                    id=run_id,
                    workflow_type="reorder",
                    requested_by=REQUESTED_BY,
                    status="RUNNING",
                    input={"sku": "ABC-42"},
                    started_at=datetime(2026, 10, 2, tzinfo=UTC),
                    plan=cyclic,
                ),
            )
        assert caught.value.code == "cycle"
        with pytest.raises(WorkflowNotFound):
            store.get_run(NORTHWIND, run_id)


def test_failed_supervisor_does_not_store_runnable_tasks(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        engine = OperationalEngine(session, planner=RejectingPlanner())
        result = engine.reorder(
            NORTHWIND,
            ReorderRequest(
                requested_by=REQUESTED_BY,
                sku="ABC-42",
                region="mx-central",
                service_level=0.9,
            ),
        )
        assert result.status.value == "FAILED"
        assert result.reason == "supervisor_iteration_limit"
        stored = WorkflowStore(session).get_run(NORTHWIND, result.run_id)
        assert stored.tasks == []
        assert stored.plan is None
        assert stored.cost_summary.supervisor_iterations == 4


def test_workflows_store_the_golden_plans(client: TestClient) -> None:
    reorder = client.post(
        f"/tenants/{NORTHWIND}/operations/reorder",
        json={
            "requested_by": REQUESTED_BY,
            "sku": "ABC-42",
            "region": "mx-central",
            "service_level": 0.9,
        },
    )
    assert reorder.status_code == 200, reorder.text
    stored = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{reorder.json()['run_id']}")
    plan = stored.json()["plan"]
    assert {task["agent"] for task in plan["tasks"]} == {
        "inventory",
        "demand",
        "supplier",
        "finance",
        "policy",
    }
    assert plan["reason"] == STARTED_REASON
    finance = next(task["id"] for task in plan["tasks"] if task["agent"] == "finance")
    finance_deps = {
        edge["depends_on"] for edge in plan["dependencies"] if edge["task_id"] == finance
    }
    assert finance_deps == {"inventory", "demand", "supplier"}
    assert stored.json()["cost_summary"]["supervisor_iterations"] == 1

    delay = client.post(
        f"/tenants/{NORTHWIND}/operations/supplier-delays",
        json={"requested_by": REQUESTED_BY, "supplier_id": "pacific-freight"},
    )
    assert delay.status_code == 200, delay.text
    delay_plan = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{delay.json()['run_id']}").json()[
        "plan"
    ]
    delay_agents = {task["agent"] for task in delay_plan["tasks"]}
    assert "finance" not in delay_agents
    assert {"supplier", "demand"} <= delay_agents

    budget = client.post(
        f"/tenants/{NORTHWIND}/operations/budget-plans",
        json={
            "requested_by": REQUESTED_BY,
            "candidates": [{"sku": "ABC-42", "region": "mx-central", "service_level": 0.9}],
        },
    )
    assert budget.status_code == 200, budget.text
    budget_plan = client.get(
        f"/tenants/{NORTHWIND}/workflow-runs/{budget.json()['run_id']}"
    ).json()["plan"]
    budget_agents = {task["agent"] for task in budget_plan["tasks"]}
    assert budget_agents != delay_agents
    assert "finance" in budget_agents
