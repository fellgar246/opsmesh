"""Domain events leave the outbox after commit and apply once."""

import json
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.events.bus import LocalEventBus, local_bus
from opsmesh_api.events.cloud import EventBridgePublisher, SqsEventSource
from opsmesh_api.events.consumers import (
    AuditProjectionConsumer,
    ProposalProcessingConsumer,
)
from opsmesh_api.events.dispatch import (
    deliver,
    publish_ready,
    publisher_for,
    reset_publisher,
    use_publisher,
)
from opsmesh_api.events.models import (
    AuditProjection,
    EvaluationSample,
    NotificationInbox,
    OutboxRow,
    ProposalEffect,
)
from opsmesh_api.events.outbox import row_to_event, stage_event
from opsmesh_api.events.schemas import DomainEvent, EventType, PublishError, WorkflowStartedPayload
from opsmesh_api.services.models import PurchaseOrder
from opsmesh_api.settings import Settings
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.workflows.models import TaskStatus, WorkflowRun, WorkflowStatus
from opsmesh_api.workflows.schemas import AgentTaskCreate, WorkflowRunCreate
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from tests.auth_tokens import bearer
from tests.test_approval_engine import MANAGER, _headers
from tests.test_draft_procurement import (
    CONTOSO,
    NORTHWIND,
    _call,
    _draft_arguments,
    _executor_task,
    _reorder,
    _stored_proposal,
)

STARTED = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
_ENVELOPE = {
    "event_id",
    "event_type",
    "tenant_id",
    "workflow_id",
    "occurred_at",
    "payload",
}


class _DownBus:
    def publish(self, event: DomainEvent) -> None:
        raise PublishError("The event bus is unavailable.")


class _PutEvents:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put_events(self, **kwargs: Any) -> dict[str, int]:
        found = kwargs["Entries"]
        assert isinstance(found, list)
        self.entries.extend(found)
        return {"FailedEntryCount": 0}


class _Queue:
    def __init__(self, bodies: list[str]) -> None:
        self._bodies = bodies

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"Messages": [{"Body": body} for body in self._bodies]}


class _FlakyAudit(AuditProjectionConsumer):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def handle(self, db: Session, event: DomainEvent) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        super().handle(db, event)


def test_golden_reorder_publishes_a_stable_sequence(client: TestClient) -> None:
    loaded = "boto3" in sys.modules
    body = _reorder(client)
    assert ("boto3" in sys.modules) is loaded
    run_id = UUID(str(body["run_id"]))
    events = local_bus().events_for(run_id)
    types = [event.event_type for event in events]
    assert types[0] is EventType.WORKFLOW_STARTED
    assert types[1:4] == [EventType.TASK_DELEGATED] * 3
    assert [event.payload["agent_type"] for event in events[1:4]] == [
        "inventory",
        "demand",
        "supplier",
    ]
    assert types[4:7] == [EventType.TASK_COMPLETED] * 3
    assert {event.payload["agent_type"] for event in events[4:7]} == {
        "inventory",
        "demand",
        "supplier",
    }
    assert types[7:] == [
        EventType.TASK_DELEGATED,
        EventType.TASK_COMPLETED,
        EventType.TASK_DELEGATED,
        EventType.TASK_COMPLETED,
        EventType.WORKFLOW_COMPLETED,
        EventType.PROPOSAL_CREATED,
        EventType.APPROVAL_REQUESTED,
    ]
    assert events[7].payload["agent_type"] == "finance"
    assert events[9].payload["agent_type"] == "policy"
    for event in events:
        assert event.tenant_id == NORTHWIND
        assert event.workflow_id == run_id
        assert "explanation" not in event.payload
        assert "transcript" not in json.dumps(event.payload)
    assert events[0].payload == {"workflow_type": "reorder"}
    assert events[11].payload["status"] == WorkflowStatus.WAITING_APPROVAL.value

    listed = client.get(f"/tenants/{NORTHWIND}/audit-events", params={"workflow_id": str(run_id)})
    assert listed.status_code == 200, listed.text
    projected = listed.json()["events"]
    assert [item["event_type"] for item in projected] == [event.value for event in types]
    other = client.get(f"/tenants/{CONTOSO}/audit-events", params={"workflow_id": str(run_id)})
    assert other.status_code == 404
    assert other.json() == {"detail": "Not found"}
    assert str(run_id) not in other.text
    scoped = client.get(
        f"/tenants/{CONTOSO}/audit-events",
        params={"workflow_id": str(run_id)},
        headers=bearer("analyst@contoso.example", "operations_analyst", CONTOSO),
    )
    assert scoped.status_code == 200
    assert scoped.json()["events"] == []
    missing = client.get("/tenants/missing/audit-events")
    assert missing.status_code == 404


def test_failed_task_does_not_publish_task_completed(
    session_factory: sessionmaker[Session],
) -> None:
    run_id, task_id = uuid4(), uuid4()
    with session_factory() as db:
        store = WorkflowStore(db)
        _open_run(store, run_id)
        store.add_task(
            NORTHWIND,
            run_id,
            AgentTaskCreate(
                id=task_id,
                agent_type="inventory",
                status=TaskStatus.PENDING,
                input={"sku": "ABC-42"},
                started_at=STARTED,
            ),
        )
        store.transition_task(
            NORTHWIND,
            run_id,
            task_id,
            status=TaskStatus.READY,
            reason="dependencies_completed",
            at=STARTED,
        )
        store.transition_task(
            NORTHWIND,
            run_id,
            task_id,
            status=TaskStatus.RUNNING,
            reason="started",
            at=STARTED,
        )
        store.transition_task(
            NORTHWIND,
            run_id,
            task_id,
            status=TaskStatus.FAILED,
            reason="specialist_failed",
            at=STARTED,
        )
    types = [event.event_type for event in local_bus().events_for(run_id)]
    assert EventType.TASK_FAILED in types
    assert EventType.TASK_COMPLETED not in types
    failed = [
        event
        for event in local_bus().events_for(run_id)
        if event.event_type is EventType.TASK_FAILED
    ]
    assert failed[-1].payload["reason"] == "specialist_failed"
    assert failed[-1].payload["task_id"] == str(task_id)


def test_publish_failure_keeps_the_run_and_retries(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = uuid4()
    token = use_publisher(_DownBus())
    try:
        with session_factory() as db:
            _open_run(WorkflowStore(db), run_id)
    finally:
        reset_publisher(token)
    with session_factory() as db:
        run = db.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status == WorkflowStatus.RUNNING.value
        rows = _outbox(db, run_id)
        assert len(rows) == 1
        assert rows[0].published_at is None
        assert rows[0].attempt_count == 1
        published = publish_ready(db)
        assert published == 1
        retried = _outbox(db, run_id)
        assert retried[0].published_at is not None
        assert _audit_count(db, run_id) == 1
        assert publish_ready(db) == 0
        assert _audit_count(db, run_id) == 1


def test_consumer_retry_projects_one_audit_row(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = uuid4()
    token = use_publisher(_DownBus())
    try:
        with session_factory() as db:
            _open_run(WorkflowStore(db), run_id)
            event = row_to_event(_outbox(db, run_id)[0])
            handler = _FlakyAudit()
            deliver(db, [event], [handler])
            assert _audit_count(db, run_id) == 0
            deliver(db, [event], [handler])
            assert _audit_count(db, run_id) == 1
            deliver(db, [event], [handler])
            assert _audit_count(db, run_id) == 1
            assert handler.calls == 3
    finally:
        reset_publisher(token)


def test_outbox_rolls_back_with_the_workflow(session_factory: sessionmaker[Session]) -> None:
    run_id = uuid4()
    with session_factory() as db:
        db.add(
            WorkflowRun(
                id=run_id,
                tenant_id=NORTHWIND,
                workflow_type="reorder",
                requested_by="analyst@northwind.example",
                status=WorkflowStatus.RUNNING.value,
                input={"sku": "ABC-42"},
                started_at=STARTED,
                finished_at=None,
                cost_summary={},
                plan=None,
                reason=None,
            )
        )
        db.flush()
        stage_event(
            db,
            event_type=EventType.WORKFLOW_STARTED,
            tenant_id=NORTHWIND,
            workflow_id=run_id,
            occurred_at=STARTED,
            payload=WorkflowStartedPayload(workflow_type="reorder"),
        )
        db.rollback()
    with session_factory() as db:
        assert db.get(WorkflowRun, run_id) is None
        assert _outbox(db, run_id) == []


def test_consumers_ignore_other_tenants(session_factory: sessionmaker[Session]) -> None:
    event = DomainEvent(
        event_id=uuid4(),
        event_type=EventType.WORKFLOW_STARTED,
        tenant_id=CONTOSO,
        workflow_id=uuid4(),
        occurred_at=STARTED,
        payload={"workflow_type": "reorder"},
    )
    with session_factory() as db:
        AuditProjectionConsumer(NORTHWIND).handle(db, event)
        stored = db.scalars(
            select(AuditProjection).where(AuditProjection.event_id == event.event_id)
        ).all()
        assert stored == []


def test_duplicate_draft_event_does_not_duplicate_the_order(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    body = _reorder(client)
    run_id = UUID(str(body["run_id"]))
    proposal = _stored_proposal(client, str(run_id))
    proposal_id = UUID(str(proposal["id"]))
    with session_factory() as db:
        before = _order_count(db)
        task_id = _executor_task(db, NORTHWIND, run_id)
        created = LocalToolRegistry(db).invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                "procurement.create_draft_purchase_order",
                _draft_arguments(proposal_id, idempotency_key="draft-event-1"),
            )
        )
        assert created.data is not None
        assert _order_count(db) == before + 1
        event = next(
            item
            for item in local_bus().events_for(run_id)
            if item.event_type is EventType.DRAFT_PO_CREATED
        )
        assert event.payload["purchase_order_id"] == created.data["purchase_order_id"]
        queue = _Queue([event.model_dump_json(), event.model_dump_json()])
        received = SqsEventSource(
            "https://sqs.us-east-1.amazonaws.com/000000000000/opsmesh",
            region="us-east-1",
            client=queue,
        ).receive()
        assert [item.event_id for item in received] == [event.event_id, event.event_id]
        deliver(db, received, [ProposalProcessingConsumer()])
        assert _order_count(db) == before + 1
        effects = db.scalars(
            select(ProposalEffect).where(
                ProposalEffect.workflow_id == run_id,
                ProposalEffect.event_type == EventType.DRAFT_PO_CREATED.value,
            )
        ).all()
        assert len(effects) == 1
        sample = db.scalar(select(EvaluationSample).where(EvaluationSample.workflow_id == run_id))
        assert sample is not None
        notes = db.scalars(
            select(NotificationInbox).where(NotificationInbox.workflow_id == run_id)
        ).all()
        assert notes


def test_cloud_publisher_uses_the_same_envelope() -> None:
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=False,
        cloud_event_bus_enabled=True,
    )
    assert isinstance(publisher_for(settings), LocalEventBus)
    enabled = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        cloud_event_bus_enabled=True,
        event_bus_name="opsmesh",
    )
    assert isinstance(publisher_for(enabled), EventBridgePublisher)
    event = DomainEvent(
        event_id=uuid4(),
        event_type=EventType.WORKFLOW_STARTED,
        tenant_id=NORTHWIND,
        workflow_id=uuid4(),
        occurred_at=STARTED,
        payload={"workflow_type": "reorder"},
    )
    sink = _PutEvents()
    EventBridgePublisher("opsmesh", region="us-east-1", client=sink).publish(event)
    assert sink.entries[0]["DetailType"] == "WorkflowStarted"
    assert sink.entries[0]["EventBusName"] == "opsmesh"
    detail = json.loads(sink.entries[0]["Detail"])
    assert set(detail) == _ENVELOPE
    assert detail["tenant_id"] == NORTHWIND
    assert detail["workflow_id"] == str(event.workflow_id)
    assert detail["payload"] == {"workflow_type": "reorder"}


def test_approval_granted_is_published(client: TestClient) -> None:
    body = _reorder(client)
    run_id = str(body["run_id"])
    proposal = _stored_proposal(client, run_id)
    approvals = proposal["approvals"]
    assert isinstance(approvals, list)
    approval_id = str(approvals[0]["id"])  # type: ignore[index]
    response = client.post(
        f"/tenants/{NORTHWIND}/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=_headers(MANAGER, "procurement_manager"),
    )
    assert response.status_code == 200, response.text
    granted = [
        event
        for event in local_bus().events_for(UUID(run_id))
        if event.event_type is EventType.APPROVAL_GRANTED
    ]
    assert len(granted) == 1
    assert granted[0].payload["actor"] == MANAGER
    assert granted[0].payload["reason"] == "granted"
    assert granted[0].tenant_id == NORTHWIND


def _open_run(store: WorkflowStore, run_id: UUID) -> None:
    store.create_run(
        NORTHWIND,
        WorkflowRunCreate(
            id=run_id,
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status=WorkflowStatus.RUNNING,
            input={"sku": "ABC-42"},
            started_at=STARTED,
        ),
    )


def _outbox(db: Session, run_id: UUID) -> list[OutboxRow]:
    return list(
        db.scalars(
            select(OutboxRow).where(OutboxRow.workflow_id == run_id).order_by(OutboxRow.sequence)
        ).all()
    )


def _audit_count(db: Session, run_id: UUID) -> int:
    counted = db.scalar(
        select(func.count())
        .select_from(AuditProjection)
        .where(AuditProjection.workflow_id == run_id)
    )
    assert isinstance(counted, int)
    return counted


def _order_count(db: Session) -> int:
    counted = db.scalar(
        select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.tenant_id == NORTHWIND)
    )
    assert isinstance(counted, int)
    return counted
