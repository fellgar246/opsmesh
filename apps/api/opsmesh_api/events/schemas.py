"""Event envelope. Payloads carry identifiers and reason codes."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    WORKFLOW_STARTED = "WorkflowStarted"
    TASK_DELEGATED = "TaskDelegated"
    TASK_COMPLETED = "TaskCompleted"
    TASK_FAILED = "TaskFailed"
    PROPOSAL_CREATED = "ProposalCreated"
    APPROVAL_REQUESTED = "ApprovalRequested"
    APPROVAL_GRANTED = "ApprovalGranted"
    APPROVAL_REJECTED = "ApprovalRejected"
    DRAFT_PO_CREATED = "DraftPOCreated"
    WORKFLOW_COMPLETED = "WorkflowCompleted"
    WORKFLOW_FAILED = "WorkflowFailed"


class DomainEvent(BaseModel):
    """One fact the system of record has already accepted."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: EventType
    tenant_id: str
    workflow_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]


class WorkflowStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_type: str


class WorkflowTerminalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    agent_type: str
    reason: str


class ProposalCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    proposal_type: str
    status: str


class ApprovalRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    proposal_id: UUID
    required_role: str


class ApprovalDecidedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    proposal_id: UUID
    required_role: str
    actor: str
    reason: str


class DraftPurchaseOrderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_id: str
    proposal_id: UUID


class AuditEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str
    tenant_id: str
    workflow_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]


class AuditEventList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[AuditEventRead] = Field(default_factory=list)


class PublishError(Exception):
    """The bus did not accept the event. The business transaction is already committed."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
