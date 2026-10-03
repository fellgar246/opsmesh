"""Managed policy is a second vote. The local engine stays the source of truth."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import AgentResultStatus, AgentType
from opsmesh_api.agents.routing import select_agent_client
from opsmesh_api.operations import policy as domain_policy
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.policy import REASON_SUPPLIER_NOT_APPROVED, approval_for_total
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.policy.gate import PolicyGatedAgentClient
from opsmesh_api.policy.managed import (
    AgentCorePolicyClient,
    ManagedPolicyAdapter,
    ManagedPolicyUnavailable,
    ManagedVote,
    select_policy_port,
)
from opsmesh_api.policy.port import (
    REASON_MANAGED_DENIED,
    REASON_MANAGED_UNAVAILABLE,
    LayerVote,
    LocalPolicyPort,
    PolicyDecision,
    PolicyEffect,
    PolicyLayer,
    PolicyRequest,
    combine,
)
from opsmesh_api.settings import Settings
from opsmesh_api.tools.gateway import (
    GatewaySpecialistConfig,
    GatewayToolAdapter,
    GatewayToolRequest,
    GatewayToolResponse,
    GatewayToolTarget,
    local_tool_definitions,
    select_tool_port,
    targets_from_definitions,
)
from opsmesh_api.tools.port import ToolCall
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.tools.schemas import CREATE_DRAFT_TOOL, SUBMIT_FOR_APPROVAL_TOOL
from opsmesh_api.workflows.models import ToolStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import AgentTaskCreate, WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
SKU = "ABC-42"
REGION = "mx-central"
UNIT_COST = 3700
STARTED = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
_DOMAIN = Path(domain_policy.__file__).resolve().parent


class ScriptedPolicy:
    """In-process managed vote. It can allow, deny, fail, or try to rewrite arguments."""

    def __init__(
        self, vote: ManagedVote | Exception, *, rewrite_quantity: int | None = None
    ) -> None:
        self._vote = vote
        self._rewrite_quantity = rewrite_quantity
        self.requests: list[PolicyRequest] = []

    def evaluate(self, request: PolicyRequest) -> ManagedVote:
        self.requests.append(request.model_copy(deep=True))
        if self._rewrite_quantity is not None:
            request.arguments["quantity"] = self._rewrite_quantity
        if isinstance(self._vote, Exception):
            raise self._vote
        return self._vote


class _QuietGateway:
    def __init__(self) -> None:
        self.calls: list[GatewayToolRequest] = []

    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        return targets_from_definitions(local_tool_definitions())

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        self.calls.append(request)
        raise AssertionError(request.tool_name)


class _PolicySdk:
    def __init__(
        self,
        *,
        status: str = "ACTIVE",
        decision: str | None = None,
        fail: bool = False,
    ) -> None:
        self.status = status
        self.decision = decision
        self.fail = fail
        self.engines = 0
        self.actions: list[dict[str, object]] = []

    def get_policy_engine(self, **kwargs: object) -> dict[str, object]:
        self.engines += 1
        if self.fail:
            raise RuntimeError("down")
        return {"status": self.status, "policyEngineId": kwargs.get("policyEngineId")}

    def authorize_action(self, **kwargs: object) -> dict[str, object]:
        self.actions.append(dict(kwargs))
        if self.decision == "DENY":
            return {"decision": "DENY", "reasonCodes": ["blocked_tool"]}
        return {"decision": "ALLOW"}


def _settings(*, enabled: bool, engine_id: str = "opsmesh-engine") -> Settings:
    return Settings(
        _env_file=None,
        aws_agentic_features_enabled=enabled,
        cloud_policy_enabled=enabled,
        policy_engine_id=engine_id,
    )


def _request(sku: str = SKU) -> ReorderRequest:
    return ReorderRequest(
        requested_by="analyst@northwind.example",
        sku=sku,
        region=REGION,
        service_level=0.90,
    )


def _allow() -> ManagedVote:
    return ManagedVote(effect=PolicyEffect.ALLOW)


def _deny() -> ManagedVote:
    return ManagedVote(effect=PolicyEffect.DENY, reason_codes=("blocked_tool",))


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
    run_id: UUID, task_id: UUID, agent: AgentType, tool_name: str, arguments: dict[str, object]
) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        agent=agent,
        tenant_id=NORTHWIND,
        workflow_run_id=run_id,
        task_id=task_id,
        arguments=arguments,
    )


def _reorder(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "requested_by": "analyst@northwind.example",
        "sku": SKU,
        "region": REGION,
        "service_level": 0.90,
    }
    payload.update(overrides)
    response = client.post(f"/tenants/{NORTHWIND}/operations/reorder", json=payload)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_flag_off_golden_reorder_does_not_construct_a_policy_client(client: TestClient) -> None:
    constructed: list[str] = []
    original = AgentCorePolicyClient.__init__

    def _spy(self: AgentCorePolicyClient, *args: object, **kwargs: object) -> None:
        constructed.append("constructed")
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    AgentCorePolicyClient.__init__ = _spy  # type: ignore[method-assign]
    try:
        loaded = "boto3" in sys.modules
        body = _reorder(client)
        assert constructed == []
        assert ("boto3" in sys.modules) is loaded
    finally:
        AgentCorePolicyClient.__init__ = original  # type: ignore[method-assign]
    assert body["quantity"] == 250
    assert body["requires_manager_approval"] is True
    assert body["requires_finance_approval"] is False
    assert body["status"] == "WAITING_APPROVAL"


def test_disabled_flag_selects_the_local_port_without_a_client() -> None:
    port = select_policy_port(_settings(enabled=False))
    assert isinstance(port, LocalPolicyPort)
    decision = port.evaluate(
        PolicyRequest(
            operation="tool",
            agent="finance",
            tenant_id=NORTHWIND,
            action="finance.get_budget",
        ),
        LayerVote(layer=PolicyLayer.LOCAL, effect=PolicyEffect.ALLOW),
    )
    assert decision.effect is PolicyEffect.ALLOW
    assert decision.layers == (LayerVote(layer=PolicyLayer.LOCAL, effect=PolicyEffect.ALLOW),)


def test_master_switch_keeps_the_adapter_disabled() -> None:
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=False,
        cloud_policy_enabled=True,
        policy_engine_id="opsmesh-engine",
    )
    assert isinstance(select_policy_port(settings), LocalPolicyPort)


def test_both_allow_runs_the_tool(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(_allow())
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        arguments = {"quantity": 250, "unit_cost_cents": UNIT_COST}
        result = registry.invoke(
            _call(run_id, task_id, AgentType.FINANCE, "finance.estimate_purchase_impact", arguments)
        )
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data["quantity"] == 250
    assert result.data["extended_cost_cents"] == 250 * UNIT_COST
    assert arguments == {"quantity": 250, "unit_cost_cents": UNIT_COST}
    assert script.requests[0].arguments["quantity"] == 250
    assert registry.handler_calls == 1


def test_local_deny_is_not_overruled_by_an_adapter_allow(
    session_factory: sessionmaker[Session],
) -> None:
    local = LayerVote(
        layer=PolicyLayer.LOCAL,
        effect=PolicyEffect.DENY,
        reason_codes=(REASON_SUPPLIER_NOT_APPROVED,),
    )
    managed = LayerVote(layer=PolicyLayer.MANAGED, effect=PolicyEffect.ALLOW)
    decision = combine(local, managed)
    assert decision.effect is PolicyEffect.DENY
    assert decision.reason_codes == (REASON_SUPPLIER_NOT_APPROVED,)
    assert [item.layer for item in decision.layers] == [PolicyLayer.LOCAL, PolicyLayer.MANAGED]

    script = ScriptedPolicy(_allow())
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.INVENTORY)
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        result = registry.invoke(
            _call(run_id, task_id, AgentType.INVENTORY, "finance.get_budget", {})
        )
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "denied"
    assert script.requests == []
    assert registry.handler_calls == 0


def test_adapter_deny_blocks_a_tool_the_local_policy_allows(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(_deny())
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        result = registry.invoke(
            _call(run_id, task_id, AgentType.FINANCE, "finance.get_budget", {})
        )
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "denied"
    assert result.error_message == "The managed policy denied the tool call."
    assert registry.handler_calls == 0
    assert script.requests[0].action == "finance.get_budget"


def test_adapter_failure_fails_closed(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(RuntimeError("down"))
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        result = registry.invoke(
            _call(run_id, task_id, AgentType.FINANCE, "finance.get_budget", {})
        )
    assert result.status == ToolStatus.FAILED
    assert result.error_code == REASON_MANAGED_UNAVAILABLE
    assert registry.handler_calls == 0


def test_adapter_does_not_rewrite_proposal_quantity(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(_allow(), rewrite_quantity=1)
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        result = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.FINANCE,
                "finance.estimate_purchase_impact",
                {"quantity": 250, "unit_cost_cents": UNIT_COST},
            )
        )
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data["quantity"] == 250
    with pytest.raises(ValidationError):
        ManagedVote.model_validate({"effect": "allow", "quantity": 1})
    with pytest.raises(ValidationError):
        PolicyDecision.model_validate(
            {
                "effect": "allow",
                "reason_codes": [],
                "layers": [{"layer": "local", "effect": "allow"}],
                "quantity": 1,
            }
        )


def test_managed_allow_keeps_the_golden_decision_and_blocks_an_unapproved_supplier(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(_allow(), rewrite_quantity=1)
    with session_factory() as db:
        client = select_agent_client(db, _settings(enabled=True), policy_transport=script)
        assert isinstance(client, PolicyGatedAgentClient)
        engine = OperationalEngine(db, agent_client=client)
        golden = engine.reorder(NORTHWIND, _request())
        blocked = engine.reorder(NORTHWIND, _request("XYZ-9"))
    assert golden.quantity == 250
    assert golden.requires_manager_approval is True
    assert golden.requires_finance_approval is False
    assert golden.status == WorkflowStatus.WAITING_APPROVAL
    assert approval_for_total(925_000) == (True, False)
    assert blocked.reason == REASON_SUPPLIER_NOT_APPROVED
    assert blocked.quantity == 0
    assert blocked.proposal_arguments is None
    assert script.requests


def test_adapter_failure_fails_the_workflow_closed(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(RuntimeError("down"))
    with session_factory() as db:
        client = select_agent_client(db, _settings(enabled=True), policy_transport=script)
        body = OperationalEngine(db, agent_client=client).reorder(NORTHWIND, _request())
    assert body.status == WorkflowStatus.FAILED
    assert body.quantity is None
    assert body.proposal_arguments is None
    assert body.requires_manager_approval is False


def test_managed_allow_does_not_skip_human_approval(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    body = _reorder(client)
    run_id = UUID(str(body["run_id"]))
    proposal = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{run_id}")
    assert proposal.status_code == 200
    stored = proposal.json()["proposals"][0]
    proposal_id = stored["id"]
    arguments = stored["arguments"]
    script = ScriptedPolicy(_allow())
    with session_factory() as db:
        task_id = uuid4()
        WorkflowStore(db).add_task(
            NORTHWIND,
            run_id,
            AgentTaskCreate(
                id=task_id,
                agent_type=AgentType.PROCUREMENT_EXECUTOR.value,
                status="RUNNING",
                input={"agent": "procurement_executor"},
                started_at=STARTED,
            ),
        )
        registry = LocalToolRegistry(db, policy=ManagedPolicyAdapter(script))
        created = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                {
                    "proposal_id": proposal_id,
                    "sku": arguments["sku"],
                    "supplier_id": arguments["supplier_id"],
                    "quantity": arguments["quantity"],
                    "unit_cost_cents": arguments["unit_cost_cents"],
                    "idempotency_key": "policy-draft",
                },
            )
        )
        assert created.status == ToolStatus.SUCCEEDED
        assert created.data is not None
        denied = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                SUBMIT_FOR_APPROVAL_TOOL,
                {
                    "purchase_order_id": created.data["purchase_order_id"],
                    "idempotency_key": "policy-submit",
                },
            )
        )
    assert denied.status == ToolStatus.FAILED
    assert denied.error_code == "approval_required"


def test_adapter_deny_does_not_call_the_gateway(
    session_factory: sessionmaker[Session],
) -> None:
    script = ScriptedPolicy(_deny())
    gateway = _QuietGateway()
    with session_factory() as db:
        run_id, task_id = _task(db, AgentType.FINANCE)
        adapter = GatewayToolAdapter(
            db,
            GatewaySpecialistConfig(endpoint="https://gateway.example/mcp", identities={}),
            gateway,
            policy=ManagedPolicyAdapter(script),
        )
        result = adapter.invoke(_call(run_id, task_id, AgentType.FINANCE, "finance.get_budget", {}))
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "denied"
    assert gateway.calls == []


def test_flag_off_tool_port_does_not_construct_a_client(
    session_factory: sessionmaker[Session],
) -> None:
    constructed: list[str] = []

    class _Spy(AgentCorePolicyClient):
        def __init__(self, engine_id: str, *, region: str, client: object = None) -> None:
            constructed.append(engine_id)
            super().__init__(engine_id, region=region, client=client)  # type: ignore[arg-type]

    original = sys.modules["opsmesh_api.policy.managed"].AgentCorePolicyClient
    sys.modules["opsmesh_api.policy.managed"].AgentCorePolicyClient = _Spy
    try:
        with session_factory() as db:
            port = select_tool_port(db, _settings(enabled=False))
        assert isinstance(port, LocalToolRegistry)
    finally:
        sys.modules["opsmesh_api.policy.managed"].AgentCorePolicyClient = original
    assert constructed == []


def test_domain_package_does_not_import_the_managed_client() -> None:
    for path in _DOMAIN.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "boto3" not in text
        assert "AgentCorePolicyClient" not in text
        assert "bedrock-agentcore-control" not in text
        assert "opsmesh_api.policy" not in text


def test_agentcore_client_fail_closed_and_votes() -> None:
    request = PolicyRequest(
        operation="tool",
        agent="finance",
        tenant_id=NORTHWIND,
        action="finance.get_budget",
    )
    with pytest.raises(ManagedPolicyUnavailable):
        AgentCorePolicyClient("", region="us-east-1").evaluate(request)
    inactive = _PolicySdk(status="CREATING")
    with pytest.raises(ManagedPolicyUnavailable):
        AgentCorePolicyClient("engine", region="us-east-1", client=inactive).evaluate(request)  # type: ignore[arg-type]
    assert inactive.engines == 1
    down = _PolicySdk(fail=True)
    with pytest.raises(ManagedPolicyUnavailable):
        AgentCorePolicyClient("engine", region="us-east-1", client=down).evaluate(request)  # type: ignore[arg-type]
    missing = _PolicySdk()
    missing.authorize_action = None  # type: ignore[method-assign]
    with pytest.raises(ManagedPolicyUnavailable):
        AgentCorePolicyClient("engine", region="us-east-1", client=missing).evaluate(request)  # type: ignore[arg-type]
    allowed = _PolicySdk(decision="ALLOW")
    vote = AgentCorePolicyClient("engine", region="us-east-1", client=allowed).evaluate(request)  # type: ignore[arg-type]
    assert vote.effect is PolicyEffect.ALLOW
    assert allowed.actions[0]["action"] == "finance.get_budget"
    denied = _PolicySdk(decision="DENY")
    vote = AgentCorePolicyClient("engine", region="us-east-1", client=denied).evaluate(request)  # type: ignore[arg-type]
    assert vote.effect is PolicyEffect.DENY
    assert vote.reason_codes == ("blocked_tool",)
    decision = ManagedPolicyAdapter(ScriptedPolicy(_deny())).evaluate(
        request,
        LayerVote(layer=PolicyLayer.LOCAL, effect=PolicyEffect.ALLOW),
    )
    assert decision.effect is PolicyEffect.DENY
    assert REASON_MANAGED_DENIED not in decision.reason_codes
    assert "blocked_tool" in decision.reason_codes
    failed = ManagedPolicyAdapter(ScriptedPolicy(RuntimeError("down"))).evaluate(
        request,
        LayerVote(layer=PolicyLayer.LOCAL, effect=PolicyEffect.ALLOW),
    )
    assert failed.effect is PolicyEffect.DENY
    assert failed.reason_codes == (REASON_MANAGED_UNAVAILABLE,)
    assert [item.layer for item in failed.layers] == [PolicyLayer.LOCAL, PolicyLayer.MANAGED]


def test_agent_deny_does_not_run_the_specialist(
    session_factory: sessionmaker[Session],
) -> None:
    from opsmesh_api.agents.contracts import AgentTask
    from opsmesh_api.agents.stubs import StubAgentClient

    script = ScriptedPolicy(_deny())
    with session_factory() as db:
        stub = StubAgentClient(db)
        client = PolicyGatedAgentClient(stub, ManagedPolicyAdapter(script))
        result = client.execute(
            AgentTask(
                task_id=uuid4(),
                agent=AgentType.INVENTORY,
                tenant_id=NORTHWIND,
                parameters={"sku": SKU, "region": REGION},
            )
        )
    assert result.status == AgentResultStatus.FAILED
    assert result.warnings[0].code == "denied"
    assert stub.tool_calls == 0


@pytest.mark.skipif(
    os.getenv("OPSMESH_LIVE_POLICY") != "1",
    reason="Live policy checks are explicit and stay out of the quality gate.",
)
def test_live_policy_engine() -> None:
    engine_id = os.getenv("OPSMESH_LIVE_POLICY_ENGINE_ID", "")
    if not engine_id:
        pytest.skip("Live policy engine is not configured")
    client = AgentCorePolicyClient(engine_id, region=os.getenv("BEDROCK_REGION", "us-east-1"))
    request = PolicyRequest(
        operation="tool",
        agent="finance",
        tenant_id=NORTHWIND,
        action="finance.get_budget",
    )
    try:
        vote = client.evaluate(request)
    except ManagedPolicyUnavailable:
        return
    assert vote.effect in {PolicyEffect.ALLOW, PolicyEffect.DENY}
