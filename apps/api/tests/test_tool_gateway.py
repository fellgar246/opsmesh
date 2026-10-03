"""Tool gateway uses the same port, schemas, and allowlists as the local registry."""

import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from opsmesh_api.agents.ceilings import MAX_TASK_RETRIES
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.settings import Settings
from opsmesh_api.tools.gateway import (
    AgentCoreGatewayClient,
    GatewayCatalogError,
    GatewayOutcome,
    GatewaySpecialistConfig,
    GatewayToolRequest,
    GatewayToolResponse,
    GatewayToolTarget,
    check_gateway_catalog,
    interpret_mcp_call,
    local_tool_definitions,
    mcp_tool_call,
    select_tool_port,
    specialist_gateway_config,
    targets_from_definitions,
)
from opsmesh_api.tools.port import ToolCall
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.workflows.models import ToolExecution, ToolStatus
from opsmesh_api.workflows.schemas import AgentTaskCreate, WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
SKU = "ABC-42"
REGION = "mx-central"
DATABASE_URL = "postgresql+psycopg://opsmesh:simulator-db-secret@localhost:5433/opsmesh"
STARTED = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)


class ScriptedGateway:
    """In-process gateway. It records the outbound call and returns a scripted response."""

    def __init__(
        self,
        targets: tuple[GatewayToolTarget, ...],
        responder: Any,
    ) -> None:
        self._targets = targets
        self._responder = responder
        self.calls: list[GatewayToolRequest] = []

    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        return self._targets

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        self.calls.append(request)
        response = self._responder(request)
        if not isinstance(response, GatewayToolResponse):
            raise RuntimeError("The tool gateway did not return a result.")
        return response


class ExplodingGateway:
    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        raise AssertionError("gateway catalog was contacted")

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        raise AssertionError(f"gateway endpoint was contacted for {request.tool_name}")


def _settings(*, master: bool, gateway: bool, identities: str = "") -> Settings:
    return Settings(
        _env_file=None,
        aws_agentic_features_enabled=master,
        cloud_tool_gateway_enabled=gateway,
        tool_gateway_endpoint="https://gateway.example/mcp",
        tool_gateway_identities=identities,
        database_url=DATABASE_URL,
    )


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


def _targets() -> tuple[GatewayToolTarget, ...]:
    return targets_from_definitions(local_tool_definitions())


def test_gateway_snapshot_matches_the_local_registry(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        call = _call(
            run_id,
            task_id,
            AgentType.INVENTORY,
            "inventory.get_snapshot",
            {"sku": SKU, "region": REGION},
        )
        local = LocalToolRegistry(db).invoke(call)
        seen: list[GatewayToolRequest] = []

        def _respond(request: GatewayToolRequest) -> GatewayToolResponse:
            seen.append(request)
            return GatewayToolResponse(
                outcome=GatewayOutcome.SUCCEEDED,
                source=local.source,
                observed_at=local.observed_at,
                data=local.data,
                result_reference=f"inventory:{SKU}:{REGION}",
            )

        gateway = ScriptedGateway(_targets(), _respond)
        port = select_tool_port(db, _settings(master=True, gateway=True), gateway)
        result = port.invoke(call)
        stored = db.get(ToolExecution, result.execution_id)
    assert local.status == ToolStatus.SUCCEEDED
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data == local.data
    assert result.source == local.source
    assert result.observed_at == local.observed_at
    assert result.data is not None
    assert result.data["sku"] == SKU
    assert stored is not None
    assert stored.status == ToolStatus.SUCCEEDED.value
    assert stored.tool_name == "inventory.get_snapshot"
    assert len(seen) == 1
    assert seen[0].agent == AgentType.INVENTORY
    assert seen[0].tenant_id == NORTHWIND
    assert seen[0].workflow_run_id == run_id
    assert seen[0].task_id == task_id
    assert seen[0].identity == "opsmesh/inventory"
    assert seen[0].credential == "opsmesh/inventory:inventory.get_snapshot"


def test_gateway_denies_a_cross_domain_tool(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.DEMAND)
        call = _call(run_id, task_id, AgentType.DEMAND, "finance.get_budget", {})

        def _respond(request: GatewayToolRequest) -> GatewayToolResponse:
            assert request.agent == AgentType.DEMAND
            assert request.tool_name == "finance.get_budget"
            return GatewayToolResponse(
                outcome=GatewayOutcome.DENIED,
                data={"available_cents": 4_200_000},
                error_message="The agent is not allowed to call this tool.",
            )

        gateway = ScriptedGateway(_targets(), _respond)
        result = select_tool_port(db, _settings(master=True, gateway=True), gateway).invoke(call)
        stored = db.get(ToolExecution, result.execution_id)
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "denied"
    assert result.data is None
    assert "available_cents" not in result.model_dump_json()
    assert stored is not None
    assert stored.status == ToolStatus.FAILED.value
    assert len(gateway.calls) == 1


def test_gateway_denies_a_cross_tenant_tool(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        call = _call(
            run_id,
            task_id,
            AgentType.FINANCE,
            "finance.get_budget",
            {"tenant_id": "contoso"},
        )

        def _respond(request: GatewayToolRequest) -> GatewayToolResponse:
            assert request.tenant_id == NORTHWIND
            return GatewayToolResponse(
                outcome=GatewayOutcome.DENIED,
                data={"available_cents": 4_200_000},
                error_message="The tenant does not match the task.",
            )

        gateway = ScriptedGateway(_targets(), _respond)
        result = select_tool_port(db, _settings(master=True, gateway=True), gateway).invoke(call)
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "denied"
    assert result.error_message == "The tenant does not match the task."
    assert result.data is None
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    ("master", "gateway_flag"),
    [(False, True), (True, False), (False, False)],
)
def test_gateway_flag_off_uses_the_local_registry(
    session_factory: sessionmaker[Session],
    master: bool,
    gateway_flag: bool,
) -> None:
    gateway = ExplodingGateway()
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        port = select_tool_port(
            db,
            _settings(master=master, gateway=gateway_flag),
            gateway,
        )
        result = port.invoke(
            _call(
                run_id,
                task_id,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                {"sku": SKU, "region": REGION},
            )
        )
    assert isinstance(port, LocalToolRegistry)
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data["sku"] == SKU


def test_catalog_mismatch_fails_before_a_tool_call(
    session_factory: sessionmaker[Session],
) -> None:
    targets = _targets()
    mismatched = targets[0].model_copy(update={"json_schema": {"type": "object"}})
    gateway = ScriptedGateway((mismatched, *targets[1:]), lambda _request: None)
    with session_factory() as db:
        with pytest.raises(GatewayCatalogError):
            select_tool_port(db, _settings(master=True, gateway=True), gateway)
    assert gateway.calls == []


def test_missing_endpoint_fails_before_a_tool_call(
    session_factory: sessionmaker[Session],
) -> None:
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        cloud_tool_gateway_enabled=True,
        database_url=DATABASE_URL,
    )
    with session_factory() as db:
        with pytest.raises(GatewayCatalogError):
            select_tool_port(db, settings)


def test_rate_limit_stays_inside_the_retry_cap(session_factory: sessionmaker[Session]) -> None:
    def _respond(_request: GatewayToolRequest) -> GatewayToolResponse:
        return GatewayToolResponse(
            outcome=GatewayOutcome.RATE_LIMITED,
            error_message="The gateway rate limit was reached.",
        )

    gateway = ScriptedGateway(_targets(), _respond)
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        result = select_tool_port(db, _settings(master=True, gateway=True), gateway).invoke(
            _call(
                run_id,
                task_id,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                {"sku": SKU, "region": REGION},
            )
        )
        rows = list(
            db.scalars(
                select(ToolExecution)
                .where(ToolExecution.task_id == task_id)
                .order_by(ToolExecution.retry_count)
            ).all()
        )
    assert len(gateway.calls) == MAX_TASK_RETRIES + 1
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "rate_limited"
    assert result.retry_count == MAX_TASK_RETRIES
    assert [row.retry_count for row in rows] == [0, 1, 2]
    assert all(row.status == ToolStatus.FAILED.value for row in rows)


def test_rate_limit_retry_can_succeed(session_factory: sessionmaker[Session]) -> None:
    attempts = {"count": 0}

    def _respond(_request: GatewayToolRequest) -> GatewayToolResponse:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return GatewayToolResponse(outcome=GatewayOutcome.RATE_LIMITED)
        return GatewayToolResponse(
            outcome=GatewayOutcome.SUCCEEDED,
            source="inventory",
            data={"sku": SKU, "region": REGION},
            result_reference=f"inventory:{SKU}:{REGION}",
        )

    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        result = select_tool_port(
            db,
            _settings(master=True, gateway=True),
            ScriptedGateway(_targets(), _respond),
        ).invoke(
            _call(
                run_id,
                task_id,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                {"sku": SKU, "region": REGION},
            )
        )
    assert attempts["count"] == 2
    assert result.status == ToolStatus.SUCCEEDED
    assert result.retry_count == 1
    assert result.data == {"sku": SKU, "region": REGION}


def test_gateway_mode_excludes_business_database_credentials(
    session_factory: sessionmaker[Session],
) -> None:
    identities = f"inventory={DATABASE_URL}"
    settings = _settings(master=True, gateway=True, identities=identities)
    config = specialist_gateway_config(settings)
    assert "database_url" not in GatewaySpecialistConfig.model_fields
    environment = config.process_environment()
    assert "DATABASE_URL" not in environment
    rendered = json.dumps(environment)
    assert "simulator-db-secret" not in rendered
    assert DATABASE_URL not in rendered
    assert "opsmesh/inventory" in environment["TOOL_GATEWAY_IDENTITIES"]

    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        gateway = ScriptedGateway(
            _targets(),
            lambda _request: GatewayToolResponse(
                outcome=GatewayOutcome.SUCCEEDED,
                source="inventory",
                data={"sku": SKU},
            ),
        )
        select_tool_port(db, settings, gateway).invoke(
            _call(
                run_id,
                task_id,
                AgentType.INVENTORY,
                "inventory.get_snapshot",
                {"sku": SKU, "region": REGION, "database_url": DATABASE_URL},
            )
        )
    sent = gateway.calls[0]
    encoded = json.dumps(sent.model_dump(mode="json"))
    assert "simulator-db-secret" not in encoded
    assert DATABASE_URL not in encoded
    assert sent.credential == "opsmesh/inventory:inventory.get_snapshot"
    headers, body = mcp_tool_call(sent)
    wire = json.dumps({"headers": headers, "body": body})
    assert "simulator-db-secret" not in wire
    assert headers["Authorization"] == "Bearer opsmesh/inventory:inventory.get_snapshot"
    assert body["params"]["_meta"]["opsmesh/workflowRunId"] == str(run_id)
    assert body["params"]["_meta"]["opsmesh/taskId"] == str(task_id)
    assert body["params"]["_meta"]["opsmesh/agent"] == "inventory"
    assert body["params"]["_meta"]["opsmesh/tenantId"] == NORTHWIND


def test_mcp_status_maps_denial_and_rate_limit() -> None:
    denied = interpret_mcp_call(403, "application/json", b"{}")
    limited = interpret_mcp_call(429, "application/json", b"{}")
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "structuredContent": {
                "source": "inventory",
                "observed_at": "2026-10-02T00:00:00+00:00",
                "data": {"sku": SKU},
                "result_reference": f"inventory:{SKU}:{REGION}",
            }
        },
    }
    parsed = interpret_mcp_call(200, "application/json", json.dumps(payload).encode())
    assert denied.outcome == GatewayOutcome.DENIED
    assert denied.data is None
    assert limited.outcome == GatewayOutcome.RATE_LIMITED
    assert limited.data is None
    assert parsed.outcome == GatewayOutcome.SUCCEEDED
    assert parsed.data == {"sku": SKU}
    assert parsed.source == "inventory"


@pytest.mark.skipif(
    os.getenv("OPSMESH_LIVE_TOOL_GATEWAY") != "1",
    reason="Live tool gateway checks are explicit and stay out of the quality gate.",
)
def test_live_tool_gateway_catalog() -> None:
    endpoint = os.getenv("OPSMESH_LIVE_TOOL_GATEWAY_ENDPOINT", "")
    if not endpoint:
        pytest.skip("Live tool gateway endpoint is not configured")
    client = AgentCoreGatewayClient(
        specialist_gateway_config(
            Settings(
                _env_file=None,
                aws_agentic_features_enabled=True,
                cloud_tool_gateway_enabled=True,
                tool_gateway_endpoint=endpoint,
            )
        )
    )
    check_gateway_catalog(local_tool_definitions(), client.list_targets())
