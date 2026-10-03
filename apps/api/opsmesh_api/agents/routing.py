"""Choose local stubs, model-backed specialists, or a remote runtime."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh_api.agents.bedrock import BedrockModelClient
from opsmesh_api.agents.contracts import AgentClient, AgentResultEnvelope, AgentTask, AgentType
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.model_port import ModelPort
from opsmesh_api.agents.planner import (
    DeterministicPlanner,
    ModelSupervisorPlanner,
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


def select_agent_client(
    db: Session,
    settings: Settings,
    model: ModelPort | None = None,
    runtime: AgentRuntimeTransport | None = None,
    local: AgentClient | None = None,
    gateway: GatewayTransport | None = None,
) -> AgentClient:
    """Use stubs unless a cloud flag is actually on."""

    resolved = local if local is not None else _local_client(db, settings, model, gateway)
    if not settings.cloud_capability(settings.cloud_agent_runtime_enabled):
        return resolved
    selected = selected_remote_agents(settings.remote_runtime_agents, CONVERSION_ORDER)
    if not selected:
        return resolved
    transport = runtime if runtime is not None else AgentCoreRuntimeClient(
        region=settings.bedrock_region,
    )
    remote = RemoteAgentClient(
        remote_endpoints(selected, settings.agent_runtime_endpoints),
        transport,
        agent_identities(settings.agent_runtime_identities, CONVERSION_ORDER),
        qualifier=settings.agent_runtime_qualifier,
    )
    return RoutedAgentClient(resolved, remote, frozenset(selected))


def _local_client(
    db: Session,
    settings: Settings,
    model: ModelPort | None,
    gateway: GatewayTransport | None = None,
) -> AgentClient:
    if not settings.cloud_capability(settings.model_provider_enabled):
        return StubAgentClient(db)
    port = model if model is not None else BedrockModelClient(region=settings.bedrock_region)
    adapter = ModelAgentAdapter(
        db,
        port,
        model_id=settings.bedrock_model_id,
        tools=select_tool_port(db, settings, gateway),
    )
    enabled = enabled_agents(settings.model_backed_agents)
    return HybridAgentClient(StubAgentClient(db), adapter, enabled)


def _count(client: AgentClient, name: str) -> int:
    value = getattr(client, name, 0)
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
    return ModelSupervisorPlanner(port, model_id=settings.bedrock_model_id)
