"""Choose local stubs, model-backed specialists, or a remote runtime."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh_api.agents.bedrock import BedrockModelClient
from opsmesh_api.agents.contracts import AgentClient, AgentResultEnvelope, AgentTask, AgentType
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.model_port import ModelPort
from opsmesh_api.agents.plan_schema import SupervisorPlan
from opsmesh_api.agents.planner import (
    DeterministicPlanner,
    ModelSupervisorPlanner,
    PlanningRequest,
    SupervisorPlanner,
)
from opsmesh_api.agents.prompts import CONVERSION_ORDER
from opsmesh_api.agents.runtime import (
    AgentCoreRuntimeClient,
    AgentRuntimeTransport,
    RemoteAgentClient,
    agent_identities,
    remote_endpoints,
    selected_remote_agents,
)
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.forecasts.port import DemandForecastProvider
from opsmesh_api.policy.gate import PolicyGatedAgentClient
from opsmesh_api.policy.managed import ManagedPolicyTransport, select_policy_port
from opsmesh_api.policy.port import LocalPolicyPort
from opsmesh_api.settings import Settings
from opsmesh_api.tools.gateway import GatewayTransport, select_tool_port


def enabled_agents(raw: str) -> tuple[AgentType, ...]:
    """Resolve the allowlist. A blank value selects the full conversion order."""

    if not raw.strip():
        return CONVERSION_ORDER
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(agent for agent in CONVERSION_ORDER if agent.value in requested)


class HybridAgentClient:
    """Send enabled specialists to the model adapter and the rest to stubs."""

    def __init__(
        self,
        stub: StubAgentClient,
        model: ModelAgentAdapter,
        enabled: tuple[AgentType, ...],
    ) -> None:
        self._stub = stub
        self._model = model
        self._enabled = frozenset(enabled)

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        if task.agent in self._enabled:
            return self._model.execute(task)
        return self._stub.execute(task)

    @property
    def tool_calls(self) -> int:
        return self._stub.tool_calls + self._model.tool_calls

    @property
    def model_calls(self) -> int:
        return self._model.model_calls

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        return self._model.provenance_for(task_id)

    @property
    def input_tokens(self) -> int:
        return _count(self._model, "input_tokens")

    @property
    def output_tokens(self) -> int:
        return _count(self._model, "output_tokens")

    @property
    def unpriced_model(self) -> bool:
        return _flag(self._model)

    @property
    def priced_model_id(self) -> str | None:
        return _model_id(self._model)


class RoutedAgentClient:
    """Send selected specialists to the remote runtime and the rest to the local client."""

    def __init__(
        self,
        local: AgentClient,
        remote: RemoteAgentClient,
        remote_agents: frozenset[AgentType],
    ) -> None:
        self._local = local
        self._remote = remote
        self._remote_agents = remote_agents

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        if task.agent in self._remote_agents:
            return self._remote.execute(task)
        return self._local.execute(task)

    @property
    def tool_calls(self) -> int:
        return _count(self._local, "tool_calls") + _count(self._remote, "tool_calls")

    @property
    def model_calls(self) -> int:
        return _count(self._local, "model_calls") + _count(self._remote, "model_calls")

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        found = _provenance(self._local, task_id)
        if found != (None, None):
            return found
        return _provenance(self._remote, task_id)

    @property
    def input_tokens(self) -> int:
        return _count(self._local, "input_tokens") + _count(self._remote, "input_tokens")

    @property
    def output_tokens(self) -> int:
        return _count(self._local, "output_tokens") + _count(self._remote, "output_tokens")

    @property
    def unpriced_model(self) -> bool:
        return _flag(self._local) or _flag(self._remote)

    @property
    def priced_model_id(self) -> str | None:
        return _model_id(self._local) or _model_id(self._remote)


class FlaggedAgentClient:
    """Use the local client for new work when the agentic cloud switch is off."""

    def __init__(self, settings: Settings, local: AgentClient, remote: AgentClient) -> None:
        self._settings = settings
        self._local = local
        self._remote = remote

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        if not self._settings.aws_agentic_features_enabled:
            return self._local.execute(task)
        return self._remote.execute(task)

    @property
    def tool_calls(self) -> int:
        return _count(self._local, "tool_calls") + _count(self._remote, "tool_calls")

    @property
    def model_calls(self) -> int:
        return _count(self._local, "model_calls") + _count(self._remote, "model_calls")

    @property
    def input_tokens(self) -> int:
        return _count(self._local, "input_tokens") + _count(self._remote, "input_tokens")

    @property
    def output_tokens(self) -> int:
        return _count(self._local, "output_tokens") + _count(self._remote, "output_tokens")

    @property
    def unpriced_model(self) -> bool:
        return _flag(self._local) or _flag(self._remote)

    @property
    def priced_model_id(self) -> str | None:
        return _model_id(self._remote) or _model_id(self._local)

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        found = _provenance(self._remote, task_id)
        if found != (None, None):
            return found
        return _provenance(self._local, task_id)


class FlaggedPlanner:
    """Plan locally when the agentic cloud switch is off, including the next call."""

    def __init__(
        self,
        settings: Settings,
        local: DeterministicPlanner,
        remote: ModelSupervisorPlanner,
    ) -> None:
        self._settings = settings
        self._local = local
        self._remote = remote
        self.iterations = 0
        self.model_calls = 0

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        active: SupervisorPlanner = (
            self._remote if self._settings.aws_agentic_features_enabled else self._local
        )
        plan = active.plan(request)
        self.iterations = active.iterations
        self.model_calls = active.model_calls
        return plan

    @property
    def input_tokens(self) -> int:
        return _count(self._local, "input_tokens") + _count(self._remote, "input_tokens")

    @property
    def output_tokens(self) -> int:
        return _count(self._local, "output_tokens") + _count(self._remote, "output_tokens")

    @property
    def unpriced_model(self) -> bool:
        return _flag(self._local) or _flag(self._remote)

    @property
    def priced_model_id(self) -> str | None:
        return _model_id(self._remote) or _model_id(self._local)


def select_agent_client(
    db: Session,
    settings: Settings,
    model: ModelPort | None = None,
    runtime: AgentRuntimeTransport | None = None,
    local: AgentClient | None = None,
    gateway: GatewayTransport | None = None,
    policy_transport: ManagedPolicyTransport | None = None,
    forecast_provider: DemandForecastProvider | None = None,
) -> AgentClient:
    """Use stubs unless a cloud flag is actually on."""

    fallback = (
        local if local is not None else StubAgentClient(db, forecast_provider=forecast_provider)
    )
    if local is not None or not settings.cloud_capability(settings.model_provider_enabled):
        resolved: AgentClient = fallback
    else:
        resolved = _local_client(db, settings, model, gateway, forecast_provider)
    if settings.cloud_capability(settings.cloud_agent_runtime_enabled):
        selected = selected_remote_agents(settings.remote_runtime_agents, CONVERSION_ORDER)
        if selected:
            transport = (
                runtime
                if runtime is not None
                else AgentCoreRuntimeClient(
                    region=settings.bedrock_region,
                )
            )
            remote = RemoteAgentClient(
                remote_endpoints(selected, settings.agent_runtime_endpoints),
                transport,
                agent_identities(settings.agent_runtime_identities, CONVERSION_ORDER),
                qualifier=settings.agent_runtime_qualifier,
            )
            resolved = RoutedAgentClient(resolved, remote, frozenset(selected))
    uses_cloud = resolved is not fallback
    resolved = _with_policy(resolved, settings, policy_transport)
    if settings.aws_agentic_features_enabled and uses_cloud:
        return FlaggedAgentClient(settings, fallback, resolved)
    return resolved


def _with_policy(
    client: AgentClient,
    settings: Settings,
    transport: ManagedPolicyTransport | None,
) -> AgentClient:
    """Leave the client unchanged unless the managed policy flag is actually on."""

    policy = select_policy_port(settings, transport)
    if isinstance(policy, LocalPolicyPort):
        return client
    return PolicyGatedAgentClient(client, policy)


def _local_client(
    db: Session,
    settings: Settings,
    model: ModelPort | None,
    gateway: GatewayTransport | None = None,
    forecast_provider: DemandForecastProvider | None = None,
) -> AgentClient:
    if not settings.cloud_capability(settings.model_provider_enabled):
        return StubAgentClient(db, forecast_provider=forecast_provider)
    port = model if model is not None else BedrockModelClient(region=settings.bedrock_region)
    adapter = ModelAgentAdapter(
        db,
        port,
        model_id=settings.bedrock_model_id,
        tools=select_tool_port(db, settings, gateway),
        forecast_provider=forecast_provider,
    )
    enabled = enabled_agents(settings.model_backed_agents)
    return HybridAgentClient(
        StubAgentClient(db, forecast_provider=forecast_provider), adapter, enabled
    )


def _flag(source: object) -> bool:
    return bool(getattr(source, "unpriced_model", False))


def _model_id(source: object) -> str | None:
    value = getattr(source, "priced_model_id", None)
    if isinstance(value, str) and value:
        return value
    return None


def _count(source: object, name: str) -> int:
    value = getattr(source, name, 0)
    if isinstance(value, int):
        return value
    return 0


def _provenance(client: AgentClient, task_id: UUID) -> tuple[str | None, str | None]:
    lookup = getattr(client, "provenance_for", None)
    if not callable(lookup):
        return None, None
    found = lookup(task_id)
    if not isinstance(found, tuple) or len(found) != 2:
        return None, None
    model_id, prompt_version = found
    if not isinstance(model_id, str) or not model_id:
        model_id = None
    if not isinstance(prompt_version, str) or not prompt_version:
        prompt_version = None
    return model_id, prompt_version


def select_planner(settings: Settings, model: ModelPort | None = None) -> SupervisorPlanner:
    """Use the deterministic planner unless the model provider flag is on."""

    if not settings.cloud_capability(settings.model_provider_enabled):
        return DeterministicPlanner()
    port = model if model is not None else BedrockModelClient(region=settings.bedrock_region)
    remote = ModelSupervisorPlanner(port, model_id=settings.bedrock_model_id)
    return FlaggedPlanner(settings, DeterministicPlanner(), remote)
