"""Choose local stubs or model-backed specialists without changing the workflow."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh_api.agents.bedrock import BedrockModelClient
from opsmesh_api.agents.contracts import AgentClient, AgentResultEnvelope, AgentTask, AgentType
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.model_port import ModelPort
from opsmesh_api.agents.prompts import CONVERSION_ORDER
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.settings import Settings


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


def select_agent_client(
    db: Session,
    settings: Settings,
    model: ModelPort | None = None,
) -> AgentClient:
    """Use stubs unless the model provider flag is actually on."""

    if not settings.cloud_capability(settings.model_provider_enabled):
        return StubAgentClient(db)
    port = model if model is not None else BedrockModelClient(region=settings.bedrock_region)
    adapter = ModelAgentAdapter(db, port, model_id=settings.bedrock_model_id)
    enabled = enabled_agents(settings.model_backed_agents)
    return HybridAgentClient(StubAgentClient(db), adapter, enabled)
