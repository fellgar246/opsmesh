"""Read-only tool registry: allowlists, schemas, and audited invocations."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.services.models import Budget, PurchaseOrder, SkuStock
from opsmesh_api.tools.catalog import READ_TOOL_SPECS, ToolSpec
from opsmesh_api.tools.port import (
    ToolCall,
    ToolPort,
    ToolRegistrationError,
    ToolResult,
)
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.tools.schemas import ToolArgs
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import ToolExecution, ToolStatus
from opsmesh_api.workflows.schemas import AgentTaskCreate, WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
REGION = "mx-central"
STARTED = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)

READ_TOOL_NAMES = frozenset(
    {
        "inventory.get_snapshot",
        "inventory.get_stockout_history",
        "inventory.get_reorder_parameters",
        "forecast.get_forecast",
        "forecast.get_forecast_quality",
        "forecast.get_demand_drivers",
        "supplier.get_terms",
        "supplier.get_reliability",
        "supplier.list_open_pos",
        "supplier.get_delay",
        "finance.get_budget",
        "finance.get_committed_spend",
        "finance.estimate_purchase_impact",
        "procurement.get_purchase_order",
        "procurement.list_purchase_orders",
        "policy.search",
        "policy.check_approval_requirement",
        "policy.check_supplier_restriction",
    }
)

FIXTURE_ARGS: dict[str, dict[str, object]] = {
    "inventory.get_snapshot": {"sku": SKU, "region": REGION},
    "inventory.get_stockout_history": {"sku": SKU, "region": REGION},
    "inventory.get_reorder_parameters": {"sku": SKU, "region": REGION},
    "forecast.get_forecast": {"sku": SKU, "region": REGION, "horizon_days": 30},
    "forecast.get_forecast_quality": {"sku": SKU, "region": REGION, "horizon_days": 30},
    "forecast.get_demand_drivers": {"sku": SKU, "region": REGION, "horizon_days": 30},
    "supplier.get_terms": {"supplier_id": "andina-supply", "sku": SKU},
    "supplier.get_reliability": {"supplier_id": "andina-supply"},
    "supplier.list_open_pos": {"supplier_id": "andina-supply"},
    "supplier.get_delay": {"supplier_id": "pacific-freight"},
    "finance.get_budget": {},
    "finance.get_committed_spend": {},
    "finance.estimate_purchase_impact": {"quantity": 250, "unit_cost_cents": 3700},
    "procurement.get_purchase_order": {"purchase_order_id": "po-northwind-1001"},
    "procurement.list_purchase_orders": {},
    "policy.search": {"extended_cost_cents": 925_000, "supplier_status": "approved"},
    "policy.check_approval_requirement": {"extended_cost_cents": 925_000},
    "policy.check_supplier_restriction": {"supplier_status": "unapproved"},
}

FOREIGN_TOOL = {
    AgentType.INVENTORY: "finance.get_budget",
    AgentType.DEMAND: "finance.get_budget",
    AgentType.SUPPLIER: "finance.get_budget",
    AgentType.FINANCE: "forecast.get_forecast",
    AgentType.POLICY: "inventory.get_snapshot",
}


def _task(db: Session, agent: AgentType) -> tuple[UUID, UUID]:
    run_id = uuid4()
    task_id = uuid4()
    store = WorkflowStore(db)
    store.create_run(
        NORTHWIND,
        WorkflowRunCreate(
            id=run_id,
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status="RUNNING",
            input={"sku": SKU},
            started_at=STARTED,
        ),
    )
    store.add_task(
        NORTHWIND,
        run_id,
        AgentTaskCreate(
            id=task_id,
            agent_type=agent.value,
            status="RUNNING",
            input={"agent": agent.value},
            started_at=STARTED,
        ),
    )
    return run_id, task_id


def _call(
    run_id: UUID,
    task_id: UUID,
    agent: AgentType,
    tool_name: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        agent=agent,
        tenant_id=NORTHWIND,
        workflow_run_id=run_id,
        task_id=task_id,
        arguments=arguments,
    )


def _enterprise_state(db: Session) -> tuple[int, int, int, int, int, int]:
    stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
    budget = db.get(Budget, NORTHWIND)
    assert stock is not None
    assert budget is not None
    orders = db.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == NORTHWIND)
    )
    assert orders is not None
    return (
        stock.on_hand,
        stock.reserved,
        stock.available,
        budget.available_cents,
        budget.committed_cents,
        int(orders),
    )


def test_catalog_is_the_read_only_surface() -> None:
    assert {spec.name for spec in READ_TOOL_SPECS} == READ_TOOL_NAMES
    assert all(spec.read_only and spec.kind == "read" for spec in READ_TOOL_SPECS)
    for name in READ_TOOL_NAMES:
        assert "create" not in name
        assert "submit" not in name


def test_registered_reads_do_not_change_enterprise_state(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        before = _enterprise_state(db)
        registry = LocalToolRegistry(db)
        assert {item.name for item in registry.definitions()} == READ_TOOL_NAMES
        assert all(item.read_only for item in registry.definitions())
        assert registry.permitted_tools(AgentType.SUPERVISOR) == ()
        for agent in (
            AgentType.INVENTORY,
            AgentType.DEMAND,
            AgentType.SUPPLIER,
            AgentType.FINANCE,
            AgentType.POLICY,
        ):
            run_id, task_id = _task(db, agent)
            for name in registry.permitted_tools(agent):
                result = registry.invoke(_call(run_id, task_id, agent, name, FIXTURE_ARGS[name]))
                assert result.status == ToolStatus.SUCCEEDED
                assert result.source
                assert result.observed_at is not None
                assert result.observed_at.utcoffset() == timedelta(0)
                assert result.data is not None
        db.expire_all()
        assert _enterprise_state(db) == before


@pytest.mark.parametrize(
    "agent",
    [
        AgentType.INVENTORY,
        AgentType.DEMAND,
        AgentType.SUPPLIER,
        AgentType.FINANCE,
        AgentType.POLICY,
    ],
)
def test_specialist_is_limited_to_its_allowlist(
    session_factory: sessionmaker[Session], agent: AgentType
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, agent)
        granted = registry.permitted_tools(agent)
        assert granted
        assert AgentType.SUPERVISOR not in {
            allowed
            for spec in READ_TOOL_SPECS
            for allowed in spec.permitted_agents
            if spec.name in granted
        }
        for name in granted:
            result = registry.invoke(_call(run_id, task_id, agent, name, FIXTURE_ARGS[name]))
            assert result.status == ToolStatus.SUCCEEDED
            assert result.arguments_hash == canonical_json_hash(FIXTURE_ARGS[name])
        foreign = FOREIGN_TOOL[agent]
        before = registry.handler_calls
        denied = registry.invoke(_call(run_id, task_id, agent, foreign, FIXTURE_ARGS[foreign]))
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        assert denied.data is None
        assert denied.source is None
        assert registry.handler_calls == before
        stored = db.get(ToolExecution, denied.execution_id)
        assert stored is not None
        assert stored.status == ToolStatus.FAILED
        assert stored.tool_name == foreign


def test_demand_can_read_the_forecast_and_not_the_budget(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.DEMAND)
        forecast = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.DEMAND,
                "forecast.get_forecast",
                FIXTURE_ARGS["forecast.get_forecast"],
            )
        )
        assert forecast.status == ToolStatus.SUCCEEDED
        assert forecast.source == "forecast"
        assert forecast.data is not None
        assert forecast.data["p50"] == 310
        assert forecast.data["p90"] == 345
        before = registry.handler_calls
        denied = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.DEMAND,
                "finance.get_budget",
                {},
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        assert denied.data is None
        assert registry.handler_calls == before
        leaked = json.dumps(denied.model_dump(mode="json"))
        assert "4200000" not in leaked
        assert "available_cents" not in leaked
        stored = db.get(ToolExecution, denied.execution_id)
        assert stored is not None
        assert stored.status == ToolStatus.FAILED


def test_supervisor_cannot_call_a_simulator_tool(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.SUPERVISOR)
        denied = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.SUPERVISOR,
                "inventory.get_snapshot",
                FIXTURE_ARGS["inventory.get_snapshot"],
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        assert denied.data is None
        assert registry.handler_calls == 0


def test_missing_sku_is_rejected_before_the_simulator(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.INVENTORY)
        result = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                {"region": REGION},
            )
        )
        assert result.status == ToolStatus.FAILED
        assert result.error_code == "invalid_arguments"
        assert result.error_field == "sku"
        assert result.error_message is not None
        assert "sku" in result.error_message
        assert result.data is None
        assert registry.handler_calls == 0
        stored = db.get(ToolExecution, result.execution_id)
        assert stored is not None
        assert stored.status == ToolStatus.FAILED


def test_tenant_argument_must_match_the_task(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.DEMAND)
        arguments = {
            "sku": SKU,
            "region": REGION,
            "horizon_days": 30,
            "tenant_id": CONTOSO,
        }
        result = registry.invoke(
            _call(run_id, task_id, AgentType.DEMAND, "forecast.get_forecast", arguments)
        )
        assert result.status == ToolStatus.FAILED
        assert result.error_code == "denied"
        assert result.data is None
        assert registry.handler_calls == 0
        leaked = json.dumps(result.model_dump(mode="json"))
        assert "310" not in leaked
        assert "345" not in leaked
        stored = db.get(ToolExecution, result.execution_id)
        assert stored is not None
        assert stored.status == ToolStatus.FAILED
        assert stored.arguments_hash == canonical_json_hash(arguments)


def test_unknown_tool_is_not_created(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        before = tuple(item.name for item in registry.definitions())
        run_id, task_id = _task(db, AgentType.DEMAND)
        result = registry.invoke(
            _call(run_id, task_id, AgentType.DEMAND, "shell.exec", {"command": "ls"})
        )
        assert result.status == ToolStatus.FAILED
        assert result.error_code == "unknown_tool"
        assert result.data is None
        assert registry.handler_calls == 0
        assert tuple(item.name for item in registry.definitions()) == before
        assert "shell.exec" not in before


def test_retries_increment_and_do_not_duplicate_success(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.DEMAND)
        arguments = FIXTURE_ARGS["forecast.get_forecast"]
        first = registry.invoke(
            _call(run_id, task_id, AgentType.DEMAND, "forecast.get_forecast", arguments)
        )
        second = registry.invoke(
            _call(run_id, task_id, AgentType.DEMAND, "forecast.get_forecast", arguments)
        )
        assert first.status == ToolStatus.SUCCEEDED
        assert second.execution_id == first.execution_id
        assert second.retry_count == 0
        assert second.data == first.data
        assert registry.handler_calls == 1
        replayed = LocalToolRegistry(db).invoke(
            _call(run_id, task_id, AgentType.DEMAND, "forecast.get_forecast", arguments)
        )
        assert replayed.execution_id == first.execution_id
        assert replayed.retry_count == 0
        success_rows = db.scalars(
            select(ToolExecution).where(
                ToolExecution.task_id == task_id,
                ToolExecution.tool_name == "forecast.get_forecast",
                ToolExecution.status == ToolStatus.SUCCEEDED.value,
            )
        ).all()
        assert len(success_rows) == 1

        inventory_run, inventory_task = _task(db, AgentType.INVENTORY)
        missing = {"sku": "NO-SUCH", "region": REGION}
        first_miss = registry.invoke(
            _call(
                inventory_run,
                inventory_task,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                missing,
            )
        )
        second_miss = registry.invoke(
            _call(
                inventory_run,
                inventory_task,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                missing,
            )
        )
        assert first_miss.status == ToolStatus.FAILED
        assert first_miss.error_code == "not_found"
        assert first_miss.retry_count == 0
        assert second_miss.retry_count == 1
        assert second_miss.execution_id != first_miss.execution_id
        misses = db.scalars(
            select(ToolExecution).where(
                ToolExecution.task_id == inventory_task,
                ToolExecution.tool_name == "inventory.get_snapshot",
            )
        ).all()
        assert [row.retry_count for row in misses] == [0, 1]


def test_policy_search_returns_rule_ids_without_citations(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.POLICY)
        found = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.POLICY,
                "policy.search",
                {"extended_cost_cents": 925_000, "supplier_status": "approved"},
            )
        )
        assert found.status == ToolStatus.SUCCEEDED
        assert found.data == {"rule_ids": ["rule.purchase_above_5000_manager_approval"]}
        empty = registry.invoke(
            _call(run_id, task_id, AgentType.POLICY, "policy.search", {})
        )
        assert empty.data == {"rule_ids": []}
        approval = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.POLICY,
                "policy.check_approval_requirement",
                {"extended_cost_cents": 2_500_001},
            )
        )
        assert approval.data is not None
        assert approval.data["requires_manager_approval"] is True
        assert approval.data["requires_finance_approval"] is True
        restricted = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.POLICY,
                "policy.check_supplier_restriction",
                {"supplier_status": "unapproved"},
            )
        )
        assert restricted.data == {
            "restricted": True,
            "rule_ids": ["rule.supplier_not_approved"],
        }
        blob = json.dumps(
            [found.model_dump(mode="json"), empty.model_dump(mode="json"), restricted.data]
        )
        for token in ("citation", "excerpt", "document", "http"):
            assert token not in blob


def test_url_sql_and_shell_tools_cannot_be_registered(
    session_factory: sessionmaker[Session],
) -> None:
    def _unused(db: Session, tenant_id: str, args: BaseModel) -> ToolResult:
        raise AssertionError((db, tenant_id, args))

    with session_factory() as db:
        for kind in ("url", "sql", "shell"):
            with pytest.raises(ToolRegistrationError):
                LocalToolRegistry(
                    db,
                    specs=(
                        ToolSpec(
                            name="custom.read",
                            argument_model=ToolArgs,
                            permitted_agents=frozenset({AgentType.INVENTORY}),
                            handler=_unused,  # type: ignore[arg-type]
                            kind=kind,
                        ),
                    ),
                )
        with pytest.raises(ToolRegistrationError):
            LocalToolRegistry(
                db,
                specs=(
                    ToolSpec(
                        name="custom.write",
                        argument_model=ToolArgs,
                        permitted_agents=frozenset({AgentType.INVENTORY}),
                        handler=_unused,  # type: ignore[arg-type]
                        read_only=False,
                    ),
                ),
            )


def test_snapshot_schema_requires_sku(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        schema = LocalToolRegistry(db).schema_for("inventory.get_snapshot")
    assert "sku" in schema["required"]
    assert "region" in schema["required"]


def test_specialist_code_can_depend_on_the_port() -> None:
    class RemoteRegistry:
        def invoke(self, call: ToolCall) -> ToolResult:
            raise AssertionError(call.tool_name)

        def permitted_tools(self, agent: AgentType) -> tuple[str, ...]:
            if agent == AgentType.DEMAND:
                return ("forecast.get_forecast",)
            return ()

        def schema_for(self, name: str) -> dict[str, object]:
            return {"type": "object", "title": name}

    def allowlist(port: ToolPort, agent: AgentType) -> tuple[str, ...]:
        return port.permitted_tools(agent)

    remote = RemoteRegistry()
    assert allowlist(remote, AgentType.DEMAND) == ("forecast.get_forecast",)
    assert allowlist(remote, AgentType.SUPERVISOR) == ()
