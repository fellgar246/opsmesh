"""EventBridge publisher and SQS consumer. Tests pass a fake client."""

import importlib
import json
from typing import Any, Protocol, cast

from opsmesh_api.events.schemas import DomainEvent, PublishError

_SOURCE = "opsmesh.domain"


class _PutEventsClient(Protocol):
    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        """Put entries on an event bus."""


class _SqsClient(Protocol):
    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        """Receive a batch from a queue."""


class EventBridgePublisher:
    """Publisher port aimed at Amazon EventBridge."""

    def __init__(
        self,
        bus_name: str,
        *,
        region: str,
        client: _PutEventsClient | None = None,
    ) -> None:
        self._bus_name = bus_name
        self._region = region
        self._client = client

    def publish(self, event: DomainEvent) -> None:
        if not self._bus_name:
            raise PublishError("The event bus name is not configured.")
        detail = event.model_dump(mode="json")
        try:
            response = self._events().put_events(
                Entries=[
                    {
                        "Source": _SOURCE,
                        "DetailType": event.event_type.value,
                        "Detail": json.dumps(detail),
                        "EventBusName": self._bus_name,
                    }
                ]
            )
        except PublishError:
            raise
        except Exception as exc:
            raise PublishError("The event bus did not accept the event.") from exc
        failed = response.get("FailedEntryCount", 0)
        if isinstance(failed, int) and failed > 0:
            raise PublishError("The event bus did not accept the event.")

    def _events(self) -> _PutEventsClient:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("boto3")
        except ImportError as exc:
            raise PublishError("The event bus client is not installed.") from exc
        factory = getattr(module, "client", None)
        if not callable(factory):
            raise PublishError("The event bus client is not installed.")
        return cast(_PutEventsClient, factory("events", region_name=self._region))


class SqsEventSource:
    """Consumer port aimed at Amazon SQS."""

    def __init__(
        self,
        queue_url: str,
        *,
        region: str,
        client: _SqsClient | None = None,
    ) -> None:
        self._queue_url = queue_url
        self._region = region
        self._client = client

    def receive(self) -> list[DomainEvent]:
        if not self._queue_url:
            raise PublishError("The event queue is not configured.")
        try:
            response = self._queue().receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=10,
            )
        except PublishError:
            raise
        except Exception as exc:
            raise PublishError("The event queue did not return messages.") from exc
        messages = response.get("Messages", [])
        if not isinstance(messages, list):
            raise PublishError("The event queue returned an unexpected result.")
        found: list[DomainEvent] = []
        for message in messages:
            if not isinstance(message, dict):
                raise PublishError("The event queue returned an unexpected result.")
            body = message.get("Body")
            if not isinstance(body, str):
                raise PublishError("The event queue returned an unexpected result.")
            found.append(DomainEvent.model_validate_json(body))
        return found

    def _queue(self) -> _SqsClient:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("boto3")
        except ImportError as exc:
            raise PublishError("The event queue client is not installed.") from exc
        factory = getattr(module, "client", None)
        if not callable(factory):
            raise PublishError("The event queue client is not installed.")
        return cast(_SqsClient, factory("sqs", region_name=self._region))
