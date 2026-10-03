"""PostgreSQL system of record for an in-flight operational run."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeEngine

from opsmesh_api.db import Base
from opsmesh_api.services.models import UtcDateTime


def _json_document() -> TypeEngine[Any]:
    return JSON().with_variant(JSONB(), "postgresql")


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ToolStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalRole(StrEnum):
    VIEWER = "viewer"
    OPERATIONS_ANALYST = "operations_analyst"
    PROCUREMENT_MANAGER = "procurement_manager"
    FINANCE_APPROVER = "finance_approver"
    ADMIN = "admin"


_WORKFLOW_STATUSES = ", ".join(f"'{item.value}'" for item in WorkflowStatus)
_TASK_STATUSES = ", ".join(f"'{item.value}'" for item in TaskStatus)
_PROPOSAL_STATUSES = ", ".join(f"'{item.value}'" for item in ProposalStatus)
_APPROVAL_STATUSES = ", ".join(f"'{item.value}'" for item in ApprovalStatus)
_TOOL_STATUSES = ", ".join(f"'{item.value}'" for item in ToolStatus)
_RISK_LEVELS = ", ".join(f"'{item.value}'" for item in RiskLevel)
_CONFIDENCE = ", ".join(f"'{item.value}'" for item in Confidence)
_ROLES = ", ".join(f"'{item.value}'" for item in ApprovalRole)


class WorkflowRun(Base):
    """One operational run for a tenant."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_WORKFLOW_STATUSES})", name="ck_workflow_runs_status"),
        Index("ix_workflow_runs_tenant_id", "tenant_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_type: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    input: Mapped[dict[str, Any]] = mapped_column(_json_document())
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    cost_summary: Mapped[dict[str, Any]] = mapped_column(_json_document())
    plan: Mapped[dict[str, Any] | None] = mapped_column(_json_document(), nullable=True)


class AgentTask(Base):
    """A specialist task inside a workflow run."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({_TASK_STATUSES})", name="ck_agent_tasks_status"),
        Index("ix_agent_tasks_run", "tenant_id", "workflow_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id"),
    )
    agent_type: Mapped[str] = mapped_column(String(64))
    parent_task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_tasks.id"),
        nullable=True,
    )
    depends_on: Mapped[list[str]] = mapped_column(_json_document())
    status: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class AgentResult(Base):
    """Append-only result envelope for one task."""

    __tablename__ = "agent_results"
    __table_args__ = (
        CheckConstraint(f"confidence IN ({_CONFIDENCE})", name="ck_agent_results_confidence"),
        Index("ix_agent_results_task", "tenant_id", "task_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("agent_tasks.id"))
    schema_version: Mapped[str] = mapped_column(String(32))
    facts: Mapped[dict[str, Any]] = mapped_column(_json_document())
    evidence: Mapped[list[Any]] = mapped_column(_json_document())
    warnings: Mapped[list[Any]] = mapped_column(_json_document())
    confidence: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Evidence(Base):
    """A cited source record attached to a workflow run."""

    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_run", "tenant_id", "workflow_run_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id"),
    )
    type: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(200))
    source_record_id: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    checksum: Mapped[str] = mapped_column(String(128))


class OperationalProposal(Base):
    """A proposal for a run. Several may exist; status marks the active one."""

    __tablename__ = "operational_proposals"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_PROPOSAL_STATUSES})",
            name="ck_operational_proposals_status",
        ),
        CheckConstraint(
            f"risk_level IN ({_RISK_LEVELS})",
            name="ck_operational_proposals_risk",
        ),
        Index("ix_operational_proposals_run", "tenant_id", "workflow_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id"),
    )
    proposal_type: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(_json_document())
    estimated_impact: Mapped[dict[str, Any]] = mapped_column(_json_document())
    risk_level: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Approval(Base):
    """A human decision bound to a hash of proposal arguments."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(f"status IN ({_APPROVAL_STATUSES})", name="ck_approvals_status"),
        CheckConstraint(f"required_role IN ({_ROLES})", name="ck_approvals_role"),
        Index("ix_approvals_proposal", "tenant_id", "proposal_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operational_proposals.id"),
    )
    arguments_hash: Mapped[str] = mapped_column(String(64))
    required_role: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ToolExecution(Base):
    """Append-only record of one tool call."""

    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint(f"status IN ({_TOOL_STATUSES})", name="ck_tool_executions_status"),
        CheckConstraint("latency_ms >= 0", name="ck_tool_executions_latency"),
        CheckConstraint("retry_count >= 0", name="ck_tool_executions_retries"),
        Index("ix_tool_executions_task", "tenant_id", "task_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("agent_tasks.id"))
    tool_name: Mapped[str] = mapped_column(String(200))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int]
    retry_count: Mapped[int] = mapped_column(server_default=text("0"))
    result_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
