"""Remote specialist runtime uses the same result contract as the local client."""

import ast
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from opsmesh_api.agents.ceilings import TASK_TIMEOUT_SECONDS
from opsmesh_api.agents.contracts import (
    FACT_MODELS,
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
    WorkflowContext,
    specialist_task,
)
from opsmesh_api.agents.prompts import CONVERSION_ORDER
from opsmesh_api.agents.routing import select_agent_client
from opsmesh_api.agents.runtime import (
    AgentCoreRuntimeClient,
    RuntimeInvocation,
    RuntimeTransportError,
    read_runtime_response,
    runtime_invoke_arguments,
    selected_remote_agents,
    session_id_for,
)
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.settings import Settings
from opsmesh_api.workflows.models import TaskStatus, WorkflowStatus
from sqlalchemy.orm import Session, sessionmaker
from tests.agent_contract import assert_result_contract

NORTHWIND = "northwind"
SKU = "ABC-42"
REGION = "mx-central"
REQUESTED_BY = "analyst@northwind.example"
CALLER_CREDENTIAL = "analyst-secret-token"
API_ROOT = Path(__file__).resolve().parents[1]

FACTS: dict[AgentType, dict[str, Any]] = {
    AgentType.INVENTORY: {
        "sku": SKU,
        "region": REGION,
        "available_units": 128,
        "reserved_units": 0,
        "on_hand_units": 128,
    },
    AgentType.DEMAND: {
        "sku": SKU,
        "region": REGION,
        "horizon_days": 30,
        "p50": 310,
        "p90": 345,
    },
    AgentType.SUPPLIER: {
        "sku": SKU,
        "supplier_id": "acme",
        "status": "approved",
        "lead_time_days": 14,
        "minimum_order_quantity": 50,
        "case_pack": 10,
        "unit_cost_cents": 3700,
        "reliability_score": 0.9,
    },
    AgentType.FINANCE: {"available_cents": 4_200_000, "committed_cents": 0},
    AgentType.POLICY: {
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
    },
}


def _request() -> ReorderRequest:
    return ReorderRequest(
        requested_by=REQUESTED_BY,
        sku=SKU,
        region=REGION,
        service_level=0.90,
    )


def _settings(agents: str, *, cloud: bool = True, master: bool = True) -> Settings:
    endpoints = ",".join(
        f"{agent.value}=arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/{agent.value}"
        for agent in CONVERSION_ORDER
    )
    identities = ",".join(f"{agent.value}=opsmesh/{agent.value}" for agent in CONVERSION_ORDER)
    return Settings(
        _env_file=None,
        aws_agentic_features_enabled=master,
        cloud_agent_runtime_enabled=cloud,
        remote_runtime_agents=agents,
        agent_runtime_endpoints=endpoints,
        agent_runtime_identities=identities,
    )


def _context() -> WorkflowContext:
    return WorkflowContext(
        workflow_id=uuid4(),
        request={"sku": SKU, "region": REGION, "service_level": 0.90},
        facts={},
        evidence_refs=[],
        completed_tasks=[],
        open_questions=[],
        proposals=[],
    )


def _task(agent: AgentType) -> AgentTask:
    parameters: dict[str, Any] = {
        AgentType.INVENTORY: {"sku": SKU, "region": REGION},
        AgentType.DEMAND: {"sku": SKU, "region": REGION, "horizon_days": 30},
        AgentType.SUPPLIER: {"sku": SKU},
        AgentType.FINANCE: {},
        AgentType.POLICY: {
            "sku": SKU,
            "extended_cost_cents": 925_000,
            "quantity": 250,
            "unit_cost_cents": 3700,
            "supplier_status": "approved",
            "reason": None,
            "blocked": False,
            "selected_quantile": "p90",
            "selected_demand": 345,
            "gross_need": 217,
        },
    }[agent]
    return specialist_task(
        task_id=uuid4(),
        agent=agent,
        tenant_id=NORTHWIND,
        parameters=parameters,
        context=_context(),
    )


def _body(task: AgentTask, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "agent": task.agent.value,
        "task_id": str(task.task_id),
        "status": "COMPLETED",
        "evidence": [
            {
                "type": "snapshot",
                "source": task.agent.value,
                "source_record_id": f"{SKU}:{REGION}",
                "observed_at": datetime(2026, 10, 2, tzinfo=UTC).isoformat(),
            }
        ],
        "facts": FACTS[task.agent],
        "warnings": [],
        "confidence": "high",
    }
    payload.update(extra)
    return payload


class RecordingRuntime:
    """In-process runtime. It records the outbound call and returns a fixture body."""

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.invocations: list[RuntimeInvocation] = []

    def invoke(self, request: RuntimeInvocation) -> dict[str, Any]:
        self.invocations.append(request)
        body = self._responder(request)
        if not isinstance(body, dict):
            raise RuntimeTransportError("The remote runtime did not return a result.")
        return body


class EchoRuntime:
    """Return the local specialist envelope as if the runtime had produced it."""

    def __init__(self, stub: StubAgentClient) -> None:
        self._stub = stub
        self.invocations: list[RuntimeInvocation] = []

    def invoke(self, request: RuntimeInvocation) -> dict[str, Any]:
        self.invocations.append(request)
        task = AgentTask.model_validate(request.payload)
        return self._stub.execute(task).model_dump(mode="json")


class ExplodingRuntime:
    def invoke(self, request: RuntimeInvocation) -> dict[str, Any]:
        raise AssertionError(f"runtime called for {request.agent.value}")


class _BytesBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Sdk:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] | None = None

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def test_remote_selection_follows_conversion_order() -> None:
    assert selected_remote_agents("", CONVERSION_ORDER) == ()
    assert selected_remote_agents("demand,policy", CONVERSION_ORDER) == (
        AgentType.POLICY,
        AgentType.DEMAND,
    )
    assert selected_remote_agents("inventory", CONVERSION_ORDER) == (AgentType.INVENTORY,)
    assert selected_remote_agents("not-an-agent", CONVERSION_ORDER) == ()


@pytest.mark.parametrize("agent", list(CONVERSION_ORDER))
def test_fake_remote_client_satisfies_the_contract(agent: AgentType) -> None:
    task = _task(agent)

    def _respond(request: RuntimeInvocation) -> dict[str, Any]:
        incoming = AgentTask.model_validate(request.payload)
        return _body(incoming)

    runtime = RecordingRuntime(_respond)
    client = _remote_client(runtime, caller_credential=CALLER_CREDENTIAL)
    result = assert_result_contract(client, task)
    assert result.status == AgentResultStatus.COMPLETED
    assert result.facts == FACTS[agent]
    assert runtime.invocations[0].timeout_seconds == TASK_TIMEOUT_SECONDS
    assert len(runtime.invocations[0].session_id) >= 33


def test_inventory_remote_result_is_persisted_as_an_inventory_envelope(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        stub = StubAgentClient(db)
        runtime = EchoRuntime(stub)
        client = select_agent_client(
            db,
            _settings("inventory"),
            runtime=runtime,
            local=stub,
        )
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
        stored = engine._store.get_run(NORTHWIND, body.run_id)
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True
    inventory = next(task for task in stored.tasks if task.agent_type == "inventory")
    result = inventory.results[0]
    envelope = AgentResultEnvelope.model_validate(
        {
            "schema_version": result.schema_version,
            "agent": inventory.agent_type,
            "task_id": str(inventory.id),
            "status": "COMPLETED",
            "evidence": [item.model_dump(mode="json") for item in result.evidence],
            "facts": result.facts,
            "warnings": [item.model_dump(mode="json") for item in result.warnings],
            "confidence": result.confidence.value,
        }
    )
    assert envelope.agent == AgentType.INVENTORY
    FACT_MODELS[AgentType.INVENTORY].model_validate(envelope.facts)
    assert {item.agent for item in runtime.invocations} == {AgentType.INVENTORY}
    assert runtime.invocations[0].agent_identity == "opsmesh/inventory"


@pytest.mark.parametrize("agent", list(CONVERSION_ORDER))
def test_one_remote_specialist_keeps_the_golden_reorder(
    session_factory: sessionmaker[Session], agent: AgentType
) -> None:
    with session_factory() as db:
        stub = StubAgentClient(db)
        runtime = EchoRuntime(stub)
        client = select_agent_client(db, _settings(agent.value), runtime=runtime, local=stub)
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
        stored = engine._store.get_run(NORTHWIND, body.run_id)
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True
    assert body.requires_finance_approval is False
    remote = next(task for task in stored.tasks if task.agent_type == agent.value)
    FACT_MODELS[agent].model_validate(remote.results[0].facts)
    assert {item.agent for item in runtime.invocations} == {agent}
    local_agents = [task for task in stored.tasks if task.agent_type != agent.value]
    assert all(task.status == TaskStatus.COMPLETED for task in local_agents)


@pytest.mark.parametrize(
    ("master", "cloud"),
    [(False, True), (True, False), (False, False)],
)
def test_cloud_agent_flag_off_does_not_call_the_runtime(
    session_factory: sessionmaker[Session], master: bool, cloud: bool
) -> None:
    runtime = ExplodingRuntime()
    with session_factory() as db:
        client = select_agent_client(
            db,
            _settings("inventory,demand,supplier,finance,policy", cloud=cloud, master=master),
            runtime=runtime,
        )
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True


def test_failing_remote_specialist_warns_transport_and_keeps_siblings(
    session_factory: sessionmaker[Session],
) -> None:
    def _down(_request: RuntimeInvocation) -> dict[str, Any]:
        raise RuntimeError("runtime unavailable")

    with session_factory() as db:
        runtime = RecordingRuntime(_down)
        client = select_agent_client(db, _settings("inventory"), runtime=runtime)
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
        stored = engine._store.get_run(NORTHWIND, body.run_id)
    assert body.status == WorkflowStatus.FAILED
    assert body.proposal_arguments is None
    by_agent = {task.agent_type: task for task in stored.tasks}
    inventory = by_agent["inventory"]
    assert inventory.status == TaskStatus.FAILED
    assert inventory.results[0].facts == {}
    assert inventory.results[0].warnings[0].code == "transport"
    assert by_agent["demand"].status == TaskStatus.COMPLETED
    assert by_agent["supplier"].status == TaskStatus.COMPLETED
    assert by_agent["demand"].results[0].facts["p90"] == 345
    assert by_agent["supplier"].results[0].facts["unit_cost_cents"] == 3700
    assert stored.proposals == []


def test_invalid_remote_payload_fails_the_task_without_changing_proposal_rules(
    session_factory: sessionmaker[Session],
) -> None:
    def _extra(request: RuntimeInvocation) -> dict[str, Any]:
        task = AgentTask.model_validate(request.payload)
        return _body(task, override_quantity=1)

    with session_factory() as db:
        runtime = RecordingRuntime(_extra)
        client = select_agent_client(db, _settings("inventory"), runtime=runtime)
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
        stored = engine._store.get_run(NORTHWIND, body.run_id)
    assert body.status == WorkflowStatus.FAILED
    assert body.quantity is None
    assert body.proposal_arguments is None
    inventory = next(task for task in stored.tasks if task.agent_type == "inventory")
    assert inventory.status == TaskStatus.FAILED
    assert inventory.results[0].facts == {}
    assert inventory.results[0].warnings[0].code == "invalid_envelope"
    assert "override_quantity" not in json.dumps(inventory.results[0].facts)
    assert stored.proposals == []
    assert approval_for_total(925_000) == (True, False)


def test_undeclared_remote_fact_is_rejected() -> None:
    task = _task(AgentType.INVENTORY)
    facts = dict(FACTS[AgentType.INVENTORY])
    facts["override_quantity"] = 1

    def _respond(request: RuntimeInvocation) -> dict[str, Any]:
        incoming = AgentTask.model_validate(request.payload)
        body = _body(incoming)
        body["facts"] = facts
        return body

    result = _remote_client(RecordingRuntime(_respond)).execute(task)
    assert result.status == AgentResultStatus.FAILED
    assert result.facts == {}
    assert result.warnings[0].code == "invalid_envelope"


def test_outbound_call_uses_the_specialist_identity() -> None:
    task = _task(AgentType.INVENTORY)
    task = task.model_copy(
        update={
            "parameters": {
                **task.parameters,
                "access_token": CALLER_CREDENTIAL,
                "requested_by": REQUESTED_BY,
            }
        }
    )
    runtime = RecordingRuntime(lambda request: _body(AgentTask.model_validate(request.payload)))
    client = _remote_client(runtime, caller_credential=CALLER_CREDENTIAL)
    result = client.execute(task)
    assert result.status == AgentResultStatus.COMPLETED
    invocation = runtime.invocations[0]
    assert invocation.agent_identity == "opsmesh/inventory"
    assert invocation.timeout_seconds == TASK_TIMEOUT_SECONDS
    arguments = runtime_invoke_arguments(invocation)
    assert "runtimeUserId" not in arguments
    encoded = arguments["payload"].decode()
    assert "opsmesh/inventory" in encoded
    assert CALLER_CREDENTIAL not in encoded
    assert REQUESTED_BY not in encoded
    assert "runtimeUserId" not in encoded


def test_missing_endpoint_does_not_call_the_runtime() -> None:
    runtime = ExplodingRuntime()
    from opsmesh_api.agents.runtime import AgentEndpoint, AgentTransport, RemoteAgentClient

    client = RemoteAgentClient(
        {
            AgentType.INVENTORY: AgentEndpoint(
                agent=AgentType.INVENTORY,
                transport=AgentTransport.REMOTE,
                endpoint_id="",
            )
        },
        runtime,
        {AgentType.INVENTORY: "opsmesh/inventory"},
    )
    result = client.execute(_task(AgentType.INVENTORY))
    assert result.status == AgentResultStatus.FAILED
    assert result.warnings[0].code == "transport"


def test_timeout_uses_the_local_task_timeout() -> None:
    task = _task(AgentType.INVENTORY)
    runtime = ExplodingRuntime()
    client = _remote_client(runtime, timeout_seconds=0)
    result = client.execute(task)
    assert result.warnings[0].code == "timeout"
    assert result.warnings[0].message == "The specialist did not finish before the deadline."
    assert result.facts == {}

    class ReadTimeoutError(Exception):
        pass

    sdk = _Sdk(error=ReadTimeoutError("timed out"))
    transport = AgentCoreRuntimeClient("us-east-1", client=sdk)
    client = _remote_client(transport, timeout_seconds=TASK_TIMEOUT_SECONDS)
    result = client.execute(task)
    assert result.warnings[0].code == "timeout"
    assert sdk.kwargs is not None
    assert sdk.kwargs["runtimeSessionId"] == session_id_for(task.task_id)
    assert "runtimeUserId" not in sdk.kwargs


def test_runtime_client_reads_json_and_sse_bodies() -> None:
    task = _task(AgentType.SUPPLIER)
    envelope = _body(task)
    sdk = _Sdk(
        response={
            "contentType": "application/json",
            "statusCode": 200,
            "response": _BytesBody(json.dumps(envelope).encode()),
        }
    )
    client = AgentCoreRuntimeClient("us-east-1", client=sdk)
    request = _invocation(task)
    assert client.invoke(request)["agent"] == "supplier"
    assert sdk.kwargs is not None
    assert json.loads(sdk.kwargs["payload"].decode())["agent_identity"] == "opsmesh/supplier"
    streamed = read_runtime_response(
        {
            "contentType": "text/event-stream",
            "statusCode": 200,
            "response": f'data: {json.dumps(envelope)}\n',
        }
    )
    assert streamed["facts"]["unit_cost_cents"] == 3700
    with pytest.raises(RuntimeTransportError):
        read_runtime_response(
            {"contentType": "application/json", "statusCode": 500, "response": b"{}"}
        )


def test_golden_workflow_does_not_import_a_cloud_sdk() -> None:
    relative_paths = (
        "opsmesh_api/agents/coordinator.py",
        "opsmesh_api/agents/fanout.py",
        "opsmesh_api/agents/stubs.py",
        "opsmesh_api/operations/engine.py",
        "opsmesh_api/operations/synthesis.py",
        "opsmesh_api/operations/policy.py",
    )
    for relative in relative_paths:
        tree = ast.parse((API_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module.split(".")[0]]
            assert "boto3" not in modules
            assert "botocore" not in modules


@pytest.mark.skipif(
    os.getenv("OPSMESH_LIVE_AGENT_RUNTIME") != "1",
    reason="Live agent runtime deployment checks are explicit and stay out of the quality gate.",
)
def test_live_agent_runtime_invocation() -> None:
    endpoint = os.getenv("OPSMESH_LIVE_AGENT_RUNTIME_ARN", "")
    if not endpoint:
        pytest.skip("Live runtime ARN is not configured")
    task = _task(AgentType.INVENTORY)
    client = AgentCoreRuntimeClient(os.getenv("BEDROCK_REGION", "us-east-1"))
    body = client.invoke(
        _invocation(task, endpoint_id=endpoint, identity="opsmesh/inventory")
    )
    envelope = AgentResultEnvelope.model_validate(body)
    assert envelope.agent == AgentType.INVENTORY


def _remote_client(
    runtime: Any,
    *,
    caller_credential: str | None = None,
    timeout_seconds: float = TASK_TIMEOUT_SECONDS,
) -> Any:
    from opsmesh_api.agents.runtime import AgentEndpoint, AgentTransport, RemoteAgentClient

    endpoints = {
        agent: AgentEndpoint(
            agent=agent,
            transport=AgentTransport.REMOTE,
            endpoint_id=f"arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/{agent.value}",
        )
        for agent in CONVERSION_ORDER
    }
    identities = {agent: f"opsmesh/{agent.value}" for agent in CONVERSION_ORDER}
    return RemoteAgentClient(
        endpoints,
        runtime,
        identities,
        timeout_seconds=timeout_seconds,
        caller_credential=caller_credential,
    )


def _invocation(
    task: AgentTask,
    *,
    endpoint_id: str = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/supplier",
    identity: str = "opsmesh/supplier",
) -> RuntimeInvocation:
    return RuntimeInvocation(
        agent=task.agent,
        endpoint_id=endpoint_id,
        agent_identity=identity,
        qualifier="DEFAULT",
        session_id=session_id_for(task.task_id),
        timeout_seconds=TASK_TIMEOUT_SECONDS,
        payload=task.model_dump(mode="json"),
    )
