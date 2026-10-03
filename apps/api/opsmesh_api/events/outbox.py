"""Write an outbox row in the caller's transaction."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh_api.events.models import OutboxRow
from opsmesh_api.events.schemas import DomainEvent, EventType


def stage_event(
    db: Session,
    *,
    event_type: EventType,
    tenant_id: str,
    workflow_id: UUID,
    occurred_at: datetime,
    payload: BaseModel,
) -> DomainEvent:
    """Add one outbox row. The caller commits it with the state change."""

    event = DomainEvent(
        event_id=uuid4(),
        event_type=event_type,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        occurred_at=occurred_at,
        payload=payload.model_dump(mode="json"),
    )
    db.add(
        OutboxRow(
            event_id=event.event_id,
            event_type=event.event_type.value,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            occurred_at=occurred_at,
            payload=event.payload,
            sequence=_next_sequence(db),
            published_at=None,
            attempt_count=0,
        )
    )
    return event


def row_to_event(row: OutboxRow) -> DomainEvent:
    return DomainEvent(
        event_id=row.event_id,
        event_type=EventType(row.event_type),
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_id,
        occurred_at=row.occurred_at,
        payload=dict(row.payload),
    )


def _next_sequence(db: Session) -> int:
    stored = db.scalar(select(func.max(OutboxRow.sequence)))
    pending = [row.sequence for row in db.new if isinstance(row, OutboxRow)]
    base = stored if isinstance(stored, int) else 0
    if pending:
        base = max(base, max(pending))
    return base + 1
