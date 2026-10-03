"""Tool port. A remote gateway can implement the same calls later."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.workflows.models import ToolStatus


class ToolTaskNotFound(Exception):
    """The invocation does not point at a task in this tenant and run."""

    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id
        super().__init__("The tool task does not exist.")


class ToolRegistrationError(Exception):
    """The tool cannot be added to the registry."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ToolNotRegistered(Exception):
    """The registry has no schema for this name."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"The tool is not registered: {name}")


class ToolCall(BaseModel):
    """One specialist invocation of a named tool."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=200)
    agent: AgentType
    tenant_id: str = Field(min_length=1)
    workflow_run_id: UUID
    task_id: UUID
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Outcome of one invocation, including evidence coordinates when it succeeds."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: ToolStatus
    arguments_hash: str
    retry_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    execution_id: UUID
    source: str | None = None
    observed_at: datetime | None = None
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_field: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """Stable name, argument schema, allowlist, and read-only flag."""

    name: str
    json_schema: dict[str, Any]
    permitted_agents: tuple[AgentType, ...]
    read_only: bool


@dataclass(frozen=True)
class HandlerResult:
    """Simulator payload plus the source and observation time agents can cite."""

    source: str
    observed_at: datetime
    data: dict[str, Any]
    result_reference: str


class ToolPort(Protocol):
    """Port specialists use for named tool calls."""

    def invoke(self, call: ToolCall) -> ToolResult:
        """Authorize, validate, run, and audit one tool call."""

    def permitted_tools(self, agent: AgentType) -> tuple[str, ...]:
        """Return the stable names this agent may call."""

    def schema_for(self, name: str) -> dict[str, Any]:
        """Return the JSON schema for one registered tool."""


ToolHandler = Callable[[Session, str, BaseModel], HandlerResult]
