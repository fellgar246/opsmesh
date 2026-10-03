"""Outbox and consumer projections. The workflow tables remain the system of record."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeEngine

from opsmesh_api.db import Base
from opsmesh_api.events.schemas import EventType
from opsmesh_api.services.models import UtcDateTime

_EVENT_TYPES = ", ".join(f"'{item.value}'" for item in EventType)


def _json_document() -> TypeEngine[Any]:
    return JSON().with_variant(JSONB(), "postgresql")


class OutboxRow(Base):
    """Event waiting for the bus. A failed publish leaves this row in place."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        CheckConstraint(f"event_type IN ({_EVENT_TYPES})", name="ck_event_outbox_type"),
        CheckConstraint("attempt_count >= 0", name="ck_event_outbox_attempts"),
        CheckConstraint("sequence >= 1", name="ck_event_outbox_sequence"),
        Index("ix_event_outbox_unpublished", "published_at", "sequence"),
        Index("ix_event_outbox_workflow", "tenant_id", "workflow_id"),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_document())
    sequence: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")


class ConsumerReceipt(Base):
    """One successful apply of an event by a consumer."""

    __tablename__ = "event_consumer_receipts"
    __table_args__ = (
        UniqueConstraint("consumer_name", "event_id", name="uq_event_consumer_receipt"),
        Index("ix_event_consumer_receipts_event", "event_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))


class AuditProjection(Base):
    """Audit view built from delivered events."""

    __tablename__ = "audit_projections"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_audit_projections_event"),
        CheckConstraint(f"event_type IN ({_EVENT_TYPES})", name="ck_audit_projections_type"),
        Index("ix_audit_projections_tenant", "tenant_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(32))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_document())
    position: Mapped[int] = mapped_column(Integer)


class NotificationInbox(Base):
    """Recorded notification. Delivery is a row, not a mail provider."""

    __tablename__ = "notification_inbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_notification_inbox_event"),
        Index("ix_notification_inbox_tenant", "tenant_id", "recorded_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime)


class AnalyticsFact(Base):
    """One analytics fact per delivered event."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_analytics_events_event"),
        Index("ix_analytics_events_tenant", "tenant_id", "event_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)


class EvaluationSample(Base):
    """A finished workflow marked for later scoring."""

    __tablename__ = "evaluation_samples"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_evaluation_samples_event"),
        UniqueConstraint("tenant_id", "workflow_id", name="uq_evaluation_samples_workflow"),
        Index("ix_evaluation_samples_tenant", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ProposalEffect(Base):
    """Proposal-processing record. A repeated event does not create another draft."""

    __tablename__ = "proposal_effects"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_proposal_effects_event"),
        Index("ix_proposal_effects_tenant", "tenant_id", "workflow_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workflow_runs.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    proposal_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    purchase_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
