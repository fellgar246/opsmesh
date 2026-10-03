"""Model-backed specialists keep tool facts and reject invalid model output."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opsmesh_api.agents.bedrock import BedrockModelClient
from opsmesh_api.agents.ceilings import (
    INPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_CEILING,
    estimate_tokens,
)
from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentResultStatus,
    AgentTask,
    AgentType,
    EvidenceRef,
)
from opsmesh_api.agents.coordinator import ReorderCoordinator
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.model_port import ModelCompletion, ModelCompletionRequest
from opsmesh_api.agents.prompts import CONVERSION_ORDER, PERMITTED_TOOLS, PROMPT_VERSIONS
from opsmesh_api.agents.routing import enabled_agents, select_agent_client
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import ReorderRead, ReorderRequest
from opsmesh_api.services.models import SkuStock
from opsmesh_api.settings import Settings
from opsmesh_api.workflows.models import TaskStatus
from opsmesh_api.workflows.schemas import AgentResultRead, AgentTaskRead, WorkflowRunRead
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker
from tests.agent_contract import assert_result_contract

NORTHWIND = "northwind"
SKU = "ABC-42"
REGION = "mx-central"
REQUESTED_BY = "analyst@northwind.example"
MODEL_ID = "fixture-model"

PLACEHOLDERS: dict[AgentType, dict[str, object]] = {
    AgentType.INVENTORY: {
        "sku": "WRONG",
        "region": "wrong",
        "available_units": 999_999,
        "reserved_units": 999_999,
        "on_hand_units": 999_999,
    },
    AgentType.DEMAND: {
        "sku": "WRONG",
        "region": "wrong",
        "horizon_days": 1,
        "p50": 1,
        "p90": 1,
    },
    AgentType.SUPPLIER: {
        "sku": "WRONG",
        "supplier_id": "wrong-supplier",
        "status": "blocked",
        "lead_time_days": 1,
        "minimum_order_quantity": 1,
        "case_pack": 1,
        "unit_cost_cents": 1,
        "reliability_score": 0.1,
    },
    AgentType.FINANCE: {"available_cents": 1, "committed_cents": 1},
    AgentType.POLICY: {
        "reason": None,
        "blocked": False,
        "selected_quantile": "p50",
        "selected_demand": 1,
        "gross_need": 1,
        "quantity": 1,
        "unit_cost_cents": 1,
        "extended_cost_cents": 1,
        "requires_manager_approval": False,
        "requires_finance_approval": False,
        "explanation": "Approval is unnecessary.",
    },
}


def _request() -> ReorderRequest:
    return ReorderRequest(
        requested_by=REQUESTED_BY,
        sku=SKU,
        region=REGION,
        service_level=0.90,
    )


def _settings(agents: str = "") -> Settings:
    return Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        model_provider_enabled=True,
        model_backed_agents=agents,
        bedrock_model_id=MODEL_ID,
    )


def _envelope(request: ModelCompletionRequest, facts: dict[str, object]) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "agent": request.agent.value,
            "task_id": str(request.task_id),
            "status": "COMPLETED",
            "evidence": [],
            "facts": facts,
            "warnings": [],
            "confidence": "high",
        }
    )


class ScriptedModel:
    """Fixture model. It never calls a provider."""

    def __init__(
        self,
        text_for: Callable[[ModelCompletionRequest], str],
        output_tokens: int | None = None,
    ) -> None:
        self.requests: list[ModelCompletionRequest] = []
        self._text_for = text_for
        self._output_tokens = output_tokens

    def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
        self.requests.append(request)
        text = self._text_for(request)
        tokens = self._output_tokens if self._output_tokens is not None else estimate_tokens(text)
        return ModelCompletion(
            text=text,
            output_tokens=tokens,
            model_id=request.model_id,
            prompt_version=request.prompt_version,
        )


def _echo(request: ModelCompletionRequest) -> str:
    return _envelope(request, dict(PLACEHOLDERS[request.agent]))


def _task(agent: AgentType) -> AgentTask:
    parameters: dict[str, object] = {
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
    return AgentTask(
        task_id=uuid4(),
        agent=agent,
        tenant_id=NORTHWIND,
        parameters=parameters,
        evidence_refs=[],
    )


def _run(
    db: Session,
    model: ScriptedModel,
    agents: str = "",
) -> tuple[ReorderRead, WorkflowRunRead]:
    client = select_agent_client(db, _settings(agents), model=model)
    engine = OperationalEngine(db, agent_client=client)
    body = engine.reorder(NORTHWIND, _request())
    stored = engine._store.get_run(NORTHWIND, body.run_id)
    return body, stored


def test_conversion_order_is_fixed() -> None:
    assert CONVERSION_ORDER == (
        AgentType.POLICY,
        AgentType.INVENTORY,
        AgentType.SUPPLIER,
        AgentType.FINANCE,
        AgentType.DEMAND,
    )
    assert enabled_agents("") == CONVERSION_ORDER
    assert enabled_agents("demand,policy") == (AgentType.POLICY, AgentType.DEMAND)
    assert enabled_agents("inventory") == (AgentType.INVENTORY,)
    assert enabled_agents("not-an-agent") == ()


@pytest.mark.parametrize("agent", list(CONVERSION_ORDER))
def test_each_model_specialist_satisfies_the_contract(
    session_factory: sessionmaker[Session], agent: AgentType
) -> None:
    with session_factory() as db:
        adapter = ModelAgentAdapter(db, ScriptedModel(_echo), model_id=MODEL_ID)
        result = assert_result_contract(adapter, _task(agent))
    assert result.facts != PLACEHOLDERS[agent]
    if agent == AgentType.INVENTORY:
        assert result.facts["available_units"] == 128
    if agent == AgentType.POLICY:
        assert result.facts["requires_manager_approval"] is True
        assert "unnecessary" not in str(result.facts["explanation"])


def test_policy_facts_keep_manager_approval_when_model_denies_it(
    session_factory: sessionmaker[Session],
) -> None:
    model = ScriptedModel(_echo)
    with session_factory() as db:
        body, stored = _run(db, model, agents="policy")
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True
    policy = next(task for task in stored.tasks if task.agent_type == "policy")
    facts = policy.results[0].facts
    assert facts["requires_manager_approval"] is True
    assert facts["requires_finance_approval"] is False
    assert "unnecessary" not in facts["explanation"]
    assert "requires manager approval" in facts["explanation"]
    assert policy.results[0].model_id == MODEL_ID
    assert policy.results[0].prompt_version == PROMPT_VERSIONS[AgentType.POLICY]
    assert all(
        task.results[0].model_id is None and task.results[0].prompt_version is None
        for task in stored.tasks
        if task.agent_type != "policy"
    )
    assert model.requests[0].permitted_tools == PERMITTED_TOOLS[AgentType.POLICY]
    assert "finance.get_budget" not in model.requests[0].user_prompt


_PARTIAL_ENVELOPE = (
    '{"facts": {"requires_manager_approval": false, '
    '"explanation": "Approval is unnecessary."}}'
)


@pytest.mark.parametrize(
    "text",
    [
        "Approval is unnecessary.",
        _PARTIAL_ENVELOPE,
    ],
)
def test_invalid_model_output_commits_no_facts(
    session_factory: sessionmaker[Session], text: str
) -> None:
    model = ScriptedModel(lambda _request: text)
    with session_factory() as db:
        body, stored = _run(db, model, agents="policy")
    assert body.status.value == "FAILED"
    policy = next(task for task in stored.tasks if task.agent_type == "policy")
    assert policy.status == TaskStatus.FAILED
    assert policy.results[0].facts == {}
    assert policy.results[0].warnings[0].code == "invalid_model_output"
    assert "unnecessary" not in json.dumps(policy.results[0].facts)
    assert stored.proposals == []
    assert policy.results[0].model_id == MODEL_ID
    assert policy.results[0].prompt_version == PROMPT_VERSIONS[AgentType.POLICY]


def test_disabled_model_flag_keeps_the_golden_proposal(
    session_factory: sessionmaker[Session],
) -> None:
    class Exploding:
        def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
            raise AssertionError("model called")

    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=False,
        model_provider_enabled=True,
    )
    with session_factory() as db:
        client = select_agent_client(db, settings, model=Exploding())
        outcome = ReorderCoordinator(client).run(
            workflow_id=uuid4(),
            tenant_id=NORTHWIND,
            request=_request(),
        )
        engine = OperationalEngine(db, agent_client=client)
        body = engine.reorder(NORTHWIND, _request())
        stored = engine._store.get_run(NORTHWIND, body.run_id)
    assert outcome.math is not None
    assert outcome.math.quantity == 250
    assert outcome.math.extended_cost_cents == 925_000
    assert outcome.manager is True
    assert outcome.finance is False
    assert outcome.model_calls == 0
    assert stored.cost_summary.model_calls == 0
    assert all(task.results[0].model_id is None for task in stored.tasks)
    assert all(task.results[0].prompt_version is None for task in stored.tasks)


def test_inventory_model_with_stub_siblings_matches_golden_proposal(
    session_factory: sessionmaker[Session],
) -> None:
    model = ScriptedModel(_echo)
    with session_factory() as db:
        body, stored = _run(db, model, agents="inventory")
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True
    inventory = next(task for task in stored.tasks if task.agent_type == "inventory")
    assert inventory.results[0].facts["available_units"] == 128
    assert inventory.results[0].facts["available_units"] != 999_999
    assert inventory.results[0].model_id == MODEL_ID
    assert inventory.results[0].prompt_version == PROMPT_VERSIONS[AgentType.INVENTORY]
    assert all(
        task.results[0].model_id is None
        for task in stored.tasks
        if task.agent_type != "inventory"
    )


def test_fixture_envelopes_validate_for_every_specialist(
    session_factory: sessionmaker[Session],
) -> None:
    model = ScriptedModel(_echo)
    with session_factory() as db:
        body, stored = _run(db, model)
    assert body.quantity == 250
    assert body.extended_cost_cents == 925_000
    assert body.requires_manager_approval is True
    assert body.cost_summary.model_calls == 5
    assert stored.cost_summary.model_calls == 5
    assert {task.agent_type for task in stored.tasks} == {
        "policy",
        "inventory",
        "supplier",
        "finance",
        "demand",
    }
    for task in stored.tasks:
        result = _stored_result(task)
        assert result.model_id == MODEL_ID
        assert result.prompt_version == PROMPT_VERSIONS[AgentType(task.agent_type)]
    finance = next(item for item in model.requests if item.agent == AgentType.FINANCE)
    assert finance.permitted_tools == (
        "finance.estimate_purchase_impact",
        "finance.get_budget",
        "finance.get_committed_spend",
    )
    assert "p90" not in finance.user_prompt
    assert "private_notes" not in finance.user_prompt
    assert finance.max_output_tokens <= OUTPUT_TOKEN_CEILING
    assert finance.max_input_tokens <= INPUT_TOKEN_CEILING
    supplier = next(item for item in model.requests if item.agent == AgentType.SUPPLIER)
    assert supplier.permitted_tools == (
        "procurement.get_purchase_order",
        "procurement.list_purchase_orders",
        "supplier.get_delay",
        "supplier.get_reliability",
        "supplier.get_terms",
        "supplier.list_open_pos",
    )
    policy = next(task for task in stored.tasks if task.agent_type == "policy")
    assert policy.results[0].facts["requires_manager_approval"] is True
    assert "unnecessary" not in policy.results[0].facts["explanation"]
    assert stored.proposals[0].arguments["quantity"] == 250


def _stored_result(task: AgentTaskRead) -> AgentResultRead:
    result = task.results[0]
    assert result.schema_version == SCHEMA_VERSION
    assert result.facts
    assert result.confidence.value in {"low", "medium", "high"}
    return result


def test_output_over_token_ceiling_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    long_text = "x" * 3601
    assert estimate_tokens(long_text) > OUTPUT_TOKEN_CEILING
    cases = [
        ScriptedModel(lambda _request: long_text, output_tokens=1),
        ScriptedModel(_echo, output_tokens=OUTPUT_TOKEN_CEILING + 1),
    ]
    for model in cases:
        with session_factory() as db:
            adapter = ModelAgentAdapter(db, model, model_id=MODEL_ID)
            result = adapter.execute(_task(AgentType.INVENTORY))
        assert result.status == AgentResultStatus.FAILED
        assert result.facts == {}
        assert result.warnings[0].code == "output_token_ceiling"
        assert 999_999 not in result.facts.values()


def test_adapter_sends_at_most_the_token_ceilings(
    session_factory: sessionmaker[Session],
) -> None:
    observed = datetime(2026, 9, 23, tzinfo=UTC)
    model = ScriptedModel(_echo)
    task = AgentTask(
        task_id=uuid4(),
        agent=AgentType.INVENTORY,
        tenant_id=NORTHWIND,
        parameters={"sku": SKU, "region": REGION},
        evidence_refs=[
            EvidenceRef(
                type="note",
                source="inventory",
                source_record_id="z" * 30_000,
                observed_at=observed,
            )
        ],
    )
    with session_factory() as db:
        ModelAgentAdapter(db, model, model_id=MODEL_ID).execute(task)
    request = model.requests[0]
    assert request.max_output_tokens <= OUTPUT_TOKEN_CEILING
    assert request.max_input_tokens <= INPUT_TOKEN_CEILING
    assert estimate_tokens(request.system_prompt) + estimate_tokens(request.user_prompt) <= (
        INPUT_TOKEN_CEILING
    )
    with pytest.raises(ValidationError):
        ModelCompletionRequest(
            model_id=MODEL_ID,
            prompt_version="policy-specialist-1",
            system_prompt="system",
            user_prompt="user",
            max_input_tokens=INPUT_TOKEN_CEILING,
            max_output_tokens=OUTPUT_TOKEN_CEILING + 1,
            permitted_tools=("policy.search",),
            task_id=uuid4(),
            agent=AgentType.POLICY,
        )


def test_missing_record_does_not_fabricate_stock_or_price(
    session_factory: sessionmaker[Session],
) -> None:
    model = ScriptedModel(_echo)
    with session_factory() as db:
        adapter = ModelAgentAdapter(db, model, model_id=MODEL_ID)
        missing = adapter.execute(
            AgentTask(
                task_id=uuid4(),
                agent=AgentType.SUPPLIER,
                tenant_id=NORTHWIND,
                parameters={"sku": "NO-SUCH"},
                evidence_refs=[],
            )
        )
        stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
        assert stock is not None
        stock.on_hand = 10
        stock.reserved = 500
        db.commit()
        conflict = adapter.execute(_task(AgentType.INVENTORY))
    assert missing.status == AgentResultStatus.FAILED
    assert missing.facts == {}
    assert "unit_cost_cents" not in missing.facts
    assert conflict.status == AgentResultStatus.PARTIAL
    assert conflict.facts == {}
    assert "available_units" not in conflict.facts
    assert conflict.warnings[0].code == "stock_conflict"
    assert model.requests == []


def test_bedrock_client_sends_the_output_ceiling() -> None:
    captured: dict[str, object] = {}

    class FakeConverse:
        def converse(self, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "output": {"message": {"content": [{"text": "{}"}]}},
                "usage": {"outputTokens": 4},
            }

    request = ModelCompletionRequest(
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSIONS[AgentType.POLICY],
        system_prompt="system",
        user_prompt="user",
        max_input_tokens=INPUT_TOKEN_CEILING,
        max_output_tokens=OUTPUT_TOKEN_CEILING,
        permitted_tools=PERMITTED_TOOLS[AgentType.POLICY],
        task_id=uuid4(),
        agent=AgentType.POLICY,
    )
    completion = BedrockModelClient(region="us-east-1", client=FakeConverse()).complete(request)
    inference = captured["inferenceConfig"]
    assert isinstance(inference, dict)
    assert inference["maxTokens"] == OUTPUT_TOKEN_CEILING
    assert inference["maxTokens"] <= OUTPUT_TOKEN_CEILING
    assert completion.model_id == MODEL_ID
    assert completion.prompt_version == PROMPT_VERSIONS[AgentType.POLICY]


def test_model_adapter_reads_the_allowlist_from_the_tool_port(
    session_factory: sessionmaker[Session],
) -> None:
    class ListingPort:
        def invoke(self, call: object) -> object:
            raise AssertionError(call)

        def permitted_tools(self, agent: AgentType) -> tuple[str, ...]:
            assert agent == AgentType.DEMAND
            return ("forecast.get_forecast",)

        def schema_for(self, name: str) -> dict[str, object]:
            return {"title": name}

    model = ScriptedModel(_echo)
    with session_factory() as db:
        ModelAgentAdapter(db, model, model_id=MODEL_ID, tools=ListingPort()).execute(
            _task(AgentType.DEMAND)
        )
    assert model.requests[0].permitted_tools == ("forecast.get_forecast",)
    assert "forecast.get_forecast" in model.requests[0].user_prompt
    assert "finance.get_budget" not in model.requests[0].user_prompt
