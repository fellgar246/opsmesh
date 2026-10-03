"""In-process bus used when cloud messaging is off."""

from uuid import UUID

from opsmesh_api.events.schemas import DomainEvent


class LocalEventBus:
    """Publisher port for the local profile. Events stay in this process."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self._events.append(event)

    def events_for(self, workflow_id: UUID) -> list[DomainEvent]:
        return [event for event in self._events if event.workflow_id == workflow_id]

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._events)


class LocalEventSource:
    """Consumer port over the in-process bus."""

    def __init__(self, bus: LocalEventBus) -> None:
        self._bus = bus
        self._cursor = 0

    def receive(self) -> list[DomainEvent]:
        fresh = list(self._bus.events[self._cursor :])
        self._cursor = len(self._bus.events)
        return fresh


_local_bus: LocalEventBus | None = None


def local_bus() -> LocalEventBus:
    global _local_bus
    if _local_bus is None:
        _local_bus = LocalEventBus()
    return _local_bus
