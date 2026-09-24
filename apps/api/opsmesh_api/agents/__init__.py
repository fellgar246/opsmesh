"""Typed specialist contracts and the in-process stub client."""

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
    WorkflowContext,
)
from opsmesh_api.agents.stubs import StubAgentClient

__all__ = [
    "SCHEMA_VERSION",
    "AgentClient",
    "AgentResultEnvelope",
    "AgentResultStatus",
    "AgentTask",
    "AgentType",
    "StubAgentClient",
    "WorkflowContext",
]
