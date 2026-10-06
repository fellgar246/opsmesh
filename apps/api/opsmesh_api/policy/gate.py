"""Apply the managed vote to agent runs and tool calls.

A local-only port adds nothing. A deny stops the operation before it runs.
The original arguments are left in place.
"""

from uuid import UUID

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
)
from opsmesh_api.policy.managed import ManagedPolicyAdapter
from opsmesh_api.policy.port import (
    REASON_MANAGED_UNAVAILABLE,
    LayerVote,
    OperationKind,
    PolicyDecision,
    PolicyEffect,
    PolicyLayer,
    PolicyRequest,
)
from opsmesh_api.tools.port import ToolCall
from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import WarningItem

_DENIED = "The managed policy denied the operation."
_TOOL_DENIED = "The managed policy denied the tool call."
_UNAVAILABLE = "The managed policy did not return a decision."


def tool_request(call: ToolCall) -> PolicyRequest:
    """Copy the tool call into a policy request. The copy is not written back."""

    return PolicyRequest(
        operation=OperationKind.TOOL,
        agent=call.agent.value,
        tenant_id=call.tenant_id,
        action=call.tool_name,
        arguments=dict(call.arguments),
    )


def managed_decision(policy: object, request: PolicyRequest) -> PolicyDecision | None:
    """Ask the managed adapter after the local allow. A local port is not asked."""

    if not isinstance(policy, ManagedPolicyAdapter):
        return None
    return policy.evaluate(request, _local_allow())


def denial_for(decision: PolicyDecision) -> tuple[str, str] | None:
    """Map a deny onto a tool error. An allow returns no error."""

    if decision.effect is not PolicyEffect.DENY:
        return None
    if REASON_MANAGED_UNAVAILABLE in decision.reason_codes:
        return REASON_MANAGED_UNAVAILABLE, _UNAVAILABLE
    return "denied", _TOOL_DENIED


class PolicyGatedAgentClient:
    """Run a specialist only when the managed vote allows it."""

    def __init__(self, inner: AgentClient, policy: ManagedPolicyAdapter) -> None:
        self._inner = inner
        self._policy = policy

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        decision = self._policy.evaluate(
            PolicyRequest(
                operation=OperationKind.AGENT,
                agent=task.agent.value,
                tenant_id=task.tenant_id,
                action=task.agent.value,
                arguments=dict(task.parameters),
            ),
            _local_allow(),
        )
        denied = denial_for(decision)
        if denied is not None:
            code, message = denied
            if code == "denied":
                message = _DENIED
            return _failed(task, code, message)
        return self._inner.execute(task)

    @property
    def tool_calls(self) -> int:
        return _count(self._inner, "tool_calls")

    @property
    def model_calls(self) -> int:
        return _count(self._inner, "model_calls")

    @property
    def input_tokens(self) -> int:
        return _count(self._inner, "input_tokens")

    @property
    def output_tokens(self) -> int:
        return _count(self._inner, "output_tokens")

    @property
    def unpriced_model(self) -> bool:
        return bool(getattr(self._inner, "unpriced_model", False))

    @property
    def priced_model_id(self) -> str | None:
        value = getattr(self._inner, "priced_model_id", None)
        if isinstance(value, str) and value:
            return value
        return None

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        lookup = getattr(self._inner, "provenance_for", None)
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


def _local_allow() -> LayerVote:
    return LayerVote(layer=PolicyLayer.LOCAL, effect=PolicyEffect.ALLOW)


def _count(client: AgentClient, name: str) -> int:
    value = getattr(client, name, 0)
    if isinstance(value, int):
        return value
    return 0


def _failed(task: AgentTask, code: str, message: str) -> AgentResultEnvelope:
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={},
        warnings=[WarningItem(code=code, message=message)],
        confidence=Confidence.LOW,
    )
