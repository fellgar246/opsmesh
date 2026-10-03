"""Consumers. Each one applies a given event id once."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh_api.events.models import (
    AnalyticsFact,
    AuditProjection,
    ConsumerReceipt,
    EvaluationSample,
    NotificationInbox,
    OutboxRow,
    ProposalEffect,
)
from opsmesh_api.events.schemas import AuditEventList, AuditEventRead, DomainEvent, EventType
from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import Tenant

_TERMINAL = frozenset({EventType.WORKFLOW_COMPLETED, EventType.WORKFLOW_FAILED})
_PROPOSAL_EVENTS = frozenset({EventType.PROPOSAL_CREATED, EventType.DRAFT_PO_CREATED})


class EventConsumer:
    """A tenant-scoped consumer. Events for other tenants are ignored."""

    name = "consumer"
    tenant_id: str | None = None

    def accepts(self, event: DomainEvent) -> bool:
        return self.tenant_id is None or self.tenant_id == event.tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        raise NotImplementedError


class AuditProjectionConsumer(EventConsumer):
    name = "audit"

    def __init__(self, tenant_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        if not self.accepts(event) or _seen(db, self.name, event.event_id):
            return
        position = _position(db, event.event_id)
        _apply(
            db,
            self.name,
            event,
            AuditProjection(
                id=uuid4(),
                event_id=event.event_id,
                event_type=event.event_type.value,
                tenant_id=event.tenant_id,
                workflow_id=event.workflow_id,
                occurred_at=event.occurred_at,
                payload=dict(event.payload),
                position=position,
            ),
        )


class NotificationConsumer(EventConsumer):
    name = "notification"

    def __init__(self, tenant_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        if not self.accepts(event) or _seen(db, self.name, event.event_id):
            return
        _apply(
            db,
            self.name,
            event,
            NotificationInbox(
                id=uuid4(),
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                workflow_id=event.workflow_id,
                event_type=event.event_type.value,
                recorded_at=event.occurred_at,
            ),
        )


class AnalyticsConsumer(EventConsumer):
    name = "analytics"

    def __init__(self, tenant_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        if not self.accepts(event) or _seen(db, self.name, event.event_id):
            return
        _apply(
            db,
            self.name,
            event,
            AnalyticsFact(
                id=uuid4(),
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                workflow_id=event.workflow_id,
                event_type=event.event_type.value,
                occurred_at=event.occurred_at,
            ),
        )


class EvaluationSampleConsumer(EventConsumer):
    """Mark a finished workflow for later scoring. The suite does not run here."""

    name = "evaluation"

    def __init__(self, tenant_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        if not self.accepts(event) or event.event_type not in _TERMINAL:
            return
        if _seen(db, self.name, event.event_id):
            return
        _apply(
            db,
            self.name,
            event,
            EvaluationSample(
                id=uuid4(),
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                workflow_id=event.workflow_id,
                occurred_at=event.occurred_at,
            ),
        )


class ProposalProcessingConsumer(EventConsumer):
    """Record proposal and draft facts. It does not insert a purchase order."""

    name = "proposal"

    def __init__(self, tenant_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    def handle(self, db: Session, event: DomainEvent) -> None:
        if not self.accepts(event) or event.event_type not in _PROPOSAL_EVENTS:
            return
        if _seen(db, self.name, event.event_id):
            return
        proposal_id = _uuid_field(event.payload.get("proposal_id"))
        purchase_order_id = event.payload.get("purchase_order_id")
        _apply(
            db,
            self.name,
            event,
            ProposalEffect(
                id=uuid4(),
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                workflow_id=event.workflow_id,
                event_type=event.event_type.value,
                proposal_id=proposal_id,
                purchase_order_id=purchase_order_id if isinstance(purchase_order_id, str) else None,
            ),
        )


def default_consumers() -> list[EventConsumer]:
    return [
        AuditProjectionConsumer(),
        NotificationConsumer(),
        AnalyticsConsumer(),
        EvaluationSampleConsumer(),
        ProposalProcessingConsumer(),
    ]


def list_audit_events(
    db: Session,
    tenant_id: str,
    workflow_id: UUID | None = None,
) -> AuditEventList:
    if db.get(Tenant, tenant_id) is None:
        raise NotFoundError()
    statement = select(AuditProjection).where(AuditProjection.tenant_id == tenant_id)
    if workflow_id is not None:
        statement = statement.where(AuditProjection.workflow_id == workflow_id)
    rows = db.scalars(statement.order_by(AuditProjection.position, AuditProjection.event_id)).all()
    return AuditEventList(
        events=[
            AuditEventRead(
                event_id=row.event_id,
                event_type=row.event_type,
                tenant_id=row.tenant_id,
                workflow_id=row.workflow_id,
                occurred_at=row.occurred_at,
                payload=dict(row.payload),
            )
            for row in rows
        ]
    )


def _seen(db: Session, consumer_name: str, event_id: UUID) -> bool:
    found = db.scalar(
        select(ConsumerReceipt.id).where(
            ConsumerReceipt.consumer_name == consumer_name,
            ConsumerReceipt.event_id == event_id,
        )
    )
    return found is not None


def _position(db: Session, event_id: UUID) -> int:
    row = db.get(OutboxRow, event_id)
    if row is None:
        return 0
    return row.sequence


def _apply(db: Session, consumer_name: str, event: DomainEvent, row: object) -> None:
    db.add(row)
    db.add(
        ConsumerReceipt(
            id=uuid4(),
            consumer_name=consumer_name,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if _seen(db, consumer_name, event.event_id):
            return
        db.add(
            ConsumerReceipt(
                id=uuid4(),
                consumer_name=consumer_name,
                event_id=event.event_id,
                tenant_id=event.tenant_id,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()


def _uuid_field(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
