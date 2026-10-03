"""Schemas validated before a workflow row is inserted."""

from datetime import UTC, datetime, timedelta
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opsmesh_api.agents.plan_schema import SupervisorPlan
from opsmesh_api.workflows.models import (
    ApprovalRole,
    ApprovalStatus,
    Confidence,
    ProposalStatus,
    RiskLevel,
    TaskStatus,
    ToolStatus,
    WorkflowStatus,
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be UTC")
    return value.astimezone(UTC)


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("JSON object required")
    return value


class CostSummary(APIModel):
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    estimated_cost_cents: int | None = Field(default=None, ge=0)
    supervisor_iterations: int = Field(default=0, ge=0)


class WarningItem(APIModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ResultEvidenceItem(APIModel):
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class WorkflowRunCreate(APIModel):
    id: UUID
    workflow_type: str = Field(min_length=1, max_length=64)
    requested_by: str = Field(min_length=1, max_length=200)
    status: WorkflowStatus
    input: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    cost_summary: CostSummary = Field(default_factory=CostSummary)
    plan: SupervisorPlan | None = None

    @field_validator("input")
    @classmethod
    def input_is_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)

    @field_validator("started_at", "finished_at")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)


class AgentTaskCreate(APIModel):
    id: UUID
    agent_type: str = Field(min_length=1, max_length=64)
    parent_task_id: UUID | None = None
    depends_on: list[UUID] = Field(default_factory=list)
    status: TaskStatus
    input: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("input")
    @classmethod
    def input_is_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)

    @field_validator("started_at", "finished_at")
    @classmethod
    def times_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def reject_self_dependency(self) -> Self:
        if self.id in self.depends_on:
            raise ValueError("task cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("dependencies must be unique")
        return self


class AgentResultCreate(APIModel):
    id: UUID
    schema_version: str = Field(min_length=1, max_length=32)
    facts: dict[str, Any]
    evidence: list[ResultEvidenceItem]
    warnings: list[WarningItem]
    confidence: Confidence
    model_id: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=64)

    @field_validator("facts")
    @classmethod
    def facts_are_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)


class EvidenceCreate(APIModel):
    id: UUID
    type: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=200)
    source_record_id: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    checksum: str = Field(min_length=1, max_length=128)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ProposalCreate(APIModel):
    id: UUID
    proposal_type: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]
    estimated_impact: dict[str, Any]
    risk_level: RiskLevel
    status: ProposalStatus
    created_at: datetime

    @field_validator("arguments", "estimated_impact")
    @classmethod
    def payloads_are_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ApprovalCreate(APIModel):
    id: UUID
    arguments: dict[str, Any]
    required_role: ApprovalRole
    status: ApprovalStatus
    expires_at: datetime
    approved_by: str | None = Field(default=None, max_length=200)

    @field_validator("arguments")
    @classmethod
    def arguments_are_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)

    @field_validator("expires_at")
    @classmethod
    def expires_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ToolExecutionCreate(APIModel):
    id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any]
    status: ToolStatus
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    result_reference: str | None = Field(default=None, max_length=200)

    @field_validator("arguments")
    @classmethod
    def arguments_are_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value)


class EvidenceRead(APIModel):
    id: UUID
    tenant_id: str
    workflow_run_id: UUID
    type: str
    source: str
    source_record_id: str
    observed_at: datetime
    checksum: str


class AgentResultRead(APIModel):
    id: UUID
    tenant_id: str
    task_id: UUID
    schema_version: str
    facts: dict[str, Any]
    evidence: list[ResultEvidenceItem]
    warnings: list[WarningItem]
    confidence: Confidence
    model_id: str | None
    prompt_version: str | None


class ToolExecutionRead(APIModel):
    id: UUID
    tenant_id: str
    task_id: UUID
    tool_name: str
    arguments_hash: str
    status: ToolStatus
    latency_ms: int
    retry_count: int
    result_reference: str | None


class AgentTaskRead(APIModel):
    id: UUID
    tenant_id: str
    workflow_run_id: UUID
    agent_type: str
    parent_task_id: UUID | None
    depends_on: list[UUID]
    status: TaskStatus
    input_hash: str
    started_at: datetime | None
    finished_at: datetime | None
    results: list[AgentResultRead]
    tool_executions: list[ToolExecutionRead]


class ApprovalRead(APIModel):
    id: UUID
    tenant_id: str
    proposal_id: UUID
    arguments_hash: str
    required_role: ApprovalRole
    status: ApprovalStatus
    expires_at: datetime
    approved_by: str | None


class ProposalRead(APIModel):
    id: UUID
    tenant_id: str
    workflow_run_id: UUID
    proposal_type: str
    arguments: dict[str, Any]
    estimated_impact: dict[str, Any]
    risk_level: RiskLevel
    status: ProposalStatus
    created_at: datetime
    approvals: list[ApprovalRead]


class WorkflowRunRead(APIModel):
    id: UUID
    tenant_id: str
    workflow_type: str
    requested_by: str
    status: WorkflowStatus
    input: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    cost_summary: CostSummary
    plan: SupervisorPlan | None = None
    tasks: list[AgentTaskRead]
    evidence: list[EvidenceRead]
    proposals: list[ProposalRead]


class WorkflowRunList(APIModel):
    workflow_runs: list[WorkflowRunRead]
