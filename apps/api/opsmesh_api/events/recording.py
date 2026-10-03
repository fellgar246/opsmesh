"""Stage domain events beside the workflow rows that caused them."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh_api.events.outbox import stage_event
from opsmesh_api.events.schemas import (
    ApprovalDecidedPayload,
    ApprovalRequestedPayload,
    DraftPurchaseOrderPayload,
    EventType,
    ProposalCreatedPayload,
    TaskPayload,
    WorkflowStartedPayload,
    WorkflowTerminalPayload,
)
from opsmesh_api.workflows.models import (
    AgentTask,
    Approval,
    OperationalProposal,
    TaskStatus,
    WorkflowRun,
    WorkflowStatus,
)

_COMPLETED = frozenset({WorkflowStatus.COMPLETED, WorkflowStatus.WAITING_APPROVAL})


def record_run_opened(db: Session, row: WorkflowRun) -> None:
    """A new run has started. A run created already finished also records that fact."""

    stage_event(
        db,
        event_type=EventType.WORKFLOW_STARTED,
        tenant_id=row.tenant_id,
        workflow_id=row.id,
        occurred_at=row.started_at,
        payload=WorkflowStartedPayload(workflow_type=row.workflow_type),
    )
    status = WorkflowStatus(row.status)
    if status not in _COMPLETED and status is not WorkflowStatus.FAILED:
        return
    event_type = (
        EventType.WORKFLOW_COMPLETED if status in _COMPLETED else EventType.WORKFLOW_FAILED
    )
    stage_event(
        db,
        event_type=event_type,
        tenant_id=row.tenant_id,
        workflow_id=row.id,
        occurred_at=row.finished_at or row.started_at,
        payload=WorkflowTerminalPayload(status=status.value, reason=row.reason),
    )


def record_run_status(
    db: Session,
    row: WorkflowRun,
    status: WorkflowStatus,
    reason: str | None,
    occurred_at: datetime,
) -> None:
    """Record a terminal status change. Waiting for approval has finished the agent run."""

    if status in _COMPLETED:
        event_type = EventType.WORKFLOW_COMPLETED
    elif status is WorkflowStatus.FAILED:
        event_type = EventType.WORKFLOW_FAILED
    else:
        return
    stage_event(
        db,
        event_type=event_type,
        tenant_id=row.tenant_id,
        workflow_id=row.id,
        occurred_at=occurred_at,
        payload=WorkflowTerminalPayload(status=status.value, reason=reason),
    )


def record_inserted_task(db: Session, task: AgentTask) -> None:
    """A task inserted already finished did not pass through delegation."""

    status = TaskStatus(task.status)
    if status is TaskStatus.COMPLETED:
        _task(db, task, EventType.TASK_COMPLETED, "completed", task.finished_at)
    elif status is TaskStatus.FAILED:
        _task(db, task, EventType.TASK_FAILED, "failed", task.finished_at)


def record_task_status(
    db: Session,
    task: AgentTask,
    status: TaskStatus,
    reason: str,
    occurred_at: datetime,
) -> None:
    if status is TaskStatus.RUNNING:
        _task(db, task, EventType.TASK_DELEGATED, reason, occurred_at)
    elif status is TaskStatus.COMPLETED:
        _task(db, task, EventType.TASK_COMPLETED, reason, occurred_at)
    elif status is TaskStatus.FAILED:
        _task(db, task, EventType.TASK_FAILED, reason, occurred_at)


def record_proposal(db: Session, row: OperationalProposal) -> None:
    stage_event(
        db,
        event_type=EventType.PROPOSAL_CREATED,
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_run_id,
        occurred_at=row.created_at,
        payload=ProposalCreatedPayload(
            proposal_id=row.id,
            proposal_type=row.proposal_type,
            status=row.status,
        ),
    )


def record_approval_requested(
    db: Session,
    row: Approval,
    workflow_id: UUID,
    occurred_at: datetime,
) -> None:
    stage_event(
        db,
        event_type=EventType.APPROVAL_REQUESTED,
        tenant_id=row.tenant_id,
        workflow_id=workflow_id,
        occurred_at=occurred_at,
        payload=ApprovalRequestedPayload(
            approval_id=row.id,
            proposal_id=row.proposal_id,
            required_role=row.required_role,
        ),
    )


def record_approval_decision(
    db: Session,
    *,
    workflow_id: UUID,
    approval: Approval,
    actor: str,
    granted: bool,
    occurred_at: datetime,
) -> None:
    stage_event(
        db,
        event_type=EventType.APPROVAL_GRANTED if granted else EventType.APPROVAL_REJECTED,
        tenant_id=approval.tenant_id,
        workflow_id=workflow_id,
        occurred_at=occurred_at,
        payload=ApprovalDecidedPayload(
            approval_id=approval.id,
            proposal_id=approval.proposal_id,
            required_role=approval.required_role,
            actor=actor,
            reason="granted" if granted else "rejected",
        ),
    )


def record_draft_purchase_order(
    db: Session,
    *,
    tenant_id: str,
    workflow_id: UUID,
    purchase_order_id: str,
    proposal_id: UUID,
    occurred_at: datetime,
) -> None:
    stage_event(
        db,
        event_type=EventType.DRAFT_PO_CREATED,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        occurred_at=occurred_at,
        payload=DraftPurchaseOrderPayload(
            purchase_order_id=purchase_order_id,
            proposal_id=proposal_id,
        ),
    )


def _task(
    db: Session,
    task: AgentTask,
    event_type: EventType,
    reason: str,
    occurred_at: datetime | None,
) -> None:
    stage_event(
        db,
        event_type=event_type,
        tenant_id=task.tenant_id,
        workflow_id=task.workflow_run_id,
        occurred_at=occurred_at or datetime.now(UTC),
        payload=TaskPayload(task_id=task.id, agent_type=task.agent_type, reason=reason),
    )
