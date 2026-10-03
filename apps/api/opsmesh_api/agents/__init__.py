"""Typed specialist contracts, local stubs, and the runtime adapter."""

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
    WorkflowContext,
)
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.stubs import StubAgentClient

__all__ = [
    "SCHEMA_VERSION",
    "AgentClient",
    "AgentResultEnvelope",
    "AgentResultStatus",
    "AgentTask",
    "AgentType",
    "ModelAgentAdapter",
    "StubAgentClient",
    "WorkflowContext",
]
