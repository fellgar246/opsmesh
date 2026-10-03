"""Publish committed outbox rows, then deliver them to local consumers."""

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.events.bus import LocalEventBus, local_bus
from opsmesh_api.events.cloud import EventBridgePublisher
from opsmesh_api.events.consumers import EventConsumer, default_consumers
from opsmesh_api.events.models import OutboxRow
from opsmesh_api.events.outbox import row_to_event
from opsmesh_api.events.schemas import DomainEvent, PublishError
from opsmesh_api.logging import get_logger
from opsmesh_api.settings import Settings, get_settings

log = get_logger("opsmesh.events")


class EventPublisher(Protocol):
    """Port that accepts one committed domain event."""

    def publish(self, event: DomainEvent) -> None:
        """Hand the event to a transport."""


class EventSource(Protocol):
    """Port that returns events for consumers."""

    def receive(self) -> list[DomainEvent]:
        """Return the next batch."""


_publisher: ContextVar[EventPublisher | None] = ContextVar("event_publisher", default=None)


def use_publisher(publisher: EventPublisher) -> Token[EventPublisher | None]:
    """Install a publisher for the current context. Tests use this to simulate a down bus."""

    return _publisher.set(publisher)


def reset_publisher(token: Token[EventPublisher | None]) -> None:
    _publisher.reset(token)


def publisher_for(settings: Settings) -> EventPublisher:
    """Local profile uses the in-process bus. Cloud uses EventBridge."""

    if settings.cloud_capability(settings.cloud_event_bus_enabled):
        return EventBridgePublisher(settings.event_bus_name, region=settings.bedrock_region)
    return local_bus()


def active_publisher() -> EventPublisher:
    override = _publisher.get()
    if override is not None:
        return override
    return publisher_for(get_settings())


def publish_ready(db: Session) -> int:
    """Publish unpublished outbox rows. A transport failure leaves the row for a later retry."""

    pending = list(
        db.scalars(
            select(OutboxRow).where(OutboxRow.published_at.is_(None)).order_by(OutboxRow.sequence)
        ).all()
    )
    events = [row_to_event(row) for row in pending]
    publisher = active_publisher()
    delivered: list[DomainEvent] = []
    for event in events:
        try:
            publisher.publish(event)
        except Exception as exc:
            _mark_attempt(db, event.event_id, published=False)
            reason = exc.message if isinstance(exc, PublishError) else "publish_failed"
            log.warning("event_publish_failed", event_id=str(event.event_id), reason=reason)
            continue
        _mark_attempt(db, event.event_id, published=True)
        delivered.append(event)
    if isinstance(publisher, LocalEventBus):
        deliver(db, delivered)
    return len(delivered)


def deliver(
    db: Session,
    events: list[DomainEvent],
    handlers: list[EventConsumer] | None = None,
) -> None:
    """Run consumers. A handler that raises can be retried and still applies once."""

    selected = default_consumers() if handlers is None else handlers
    for event in events:
        for handler in selected:
            if not handler.accepts(event):
                continue
            try:
                handler.handle(db, event)
            except Exception:
                db.rollback()
                log.warning(
                    "event_consumer_failed",
                    event_id=str(event.event_id),
                    consumer=handler.name,
                )


def _mark_attempt(db: Session, event_id: UUID, *, published: bool) -> None:
    row = db.get(OutboxRow, event_id)
    if row is None:
        return
    row.attempt_count += 1
    if published:
        row.published_at = datetime.now(UTC)
    db.commit()
