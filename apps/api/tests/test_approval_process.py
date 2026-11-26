"""Approval wait. The local machine drives approve, reject, and timeout."""

import ast
import importlib.util
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from opsmesh_api.agents.planner import DeterministicPlanner, ModelSupervisorPlanner
from opsmesh_api.approvals.engine import APPROVAL_TTL, Actor, ApprovalService
from opsmesh_api.events.bus import local_bus
from opsmesh_api.events.models import NotificationInbox, OutboxRow
from opsmesh_api.events.schemas import EventType
from opsmesh_api.services.models import PurchaseOrder
from opsmesh_api.services.procurement import ProcurementService
from opsmesh_api.settings import Settings
from opsmesh_api.workflows.approval_machine import (
    ApprovalProcessError,
    LocalApprovalProcess,
    ProcessSignal,
    runner_for,
)
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import (
    Approval,
    ApprovalProcess,
    ApprovalProcessState,
    ApprovalRole,
    ApprovalStatus,
    OperationalProposal,
    ProcessSignalKind,
    ProposalStatus,
)
from opsmesh_api.workflows.schemas import ApprovalCreate, ProposalCreate, WorkflowRunCreate
from opsmesh_api.workflows.state_machine_cloud import StepFunctionsApprovalProcess
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
SKU = "ABC-42"
SUPPLIER = "andina-supply"
UNIT_COST = 3700
STARTED = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
_MODEL_MODULES = frozenset(
    {
        "boto3",
        "opsmesh_api.agents.planner",
        "opsmesh_api.agents.model_adapter",
        "opsmesh_api.agents.model_port",
        "opsmesh_api.agents.runtime",
    }
)


class _Started:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def start_execution(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"executionArn": "arn:aws:states:us-east-1:0:execution:approval:1"}


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_approve_creates_one_draft_and_publishes_a_notification(db: Session) -> None:
    proposal_id, run_id, arguments_hash = _waiting(db)
    _decide(db, proposal_id, "approve")
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)

    first = machine.signal(NORTHWIND, proposal_id, _approve(arguments_hash), now=STARTED)
    second = machine.signal(NORTHWIND, proposal_id, _approve(arguments_hash), now=STARTED)

    assert first.state == ApprovalProcessState.NOTIFIED.value
    assert first.last_signal == ProcessSignalKind.APPROVE.value
    assert second.id == first.id
    assert second.state == ApprovalProcessState.NOTIFIED.value
    assert len(_orders(db, proposal_id)) == 1
    assert len(_notifications(db, run_id)) == 1
    drafts = [
        event
        for event in local_bus().events_for(run_id)
        if event.event_type is EventType.DRAFT_PO_CREATED
    ]
    assert len(drafts) == 1


def test_duplicate_approve_does_not_call_draft_again(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal_id, _run_id, arguments_hash = _waiting(db)
    calls: list[str] = []
    original = ProcurementService.create_draft_purchase_order

    def _record(self: ProcurementService, tenant_id: str, **kwargs: Any) -> Any:
        calls.append(str(kwargs["proposal_id"]))
        return original(self, tenant_id, **kwargs)

    monkeypatch.setattr(ProcurementService, "create_draft_purchase_order", _record)
    _decide(db, proposal_id, "approve")
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)
    machine.signal(NORTHWIND, proposal_id, _approve(arguments_hash), now=STARTED)
    machine.signal(NORTHWIND, proposal_id, _approve(arguments_hash), now=STARTED)

    assert calls == [str(proposal_id)]
    assert len(_orders(db, proposal_id)) == 1


def test_timeout_expires_the_proposal_without_a_draft(db: Session) -> None:
    proposal_id, _run_id, arguments_hash = _waiting(db)
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)

    early = machine.signal(
        NORTHWIND,
        proposal_id,
        _timeout(arguments_hash),
        now=STARTED + APPROVAL_TTL - timedelta(seconds=1),
    )
    assert early.state == ApprovalProcessState.WAITING.value
    assert _proposal(db, proposal_id).status == ProposalStatus.PENDING_APPROVAL.value

    expired = machine.signal(
        NORTHWIND,
        proposal_id,
        _timeout(arguments_hash),
        now=STARTED + APPROVAL_TTL,
    )
    again = machine.signal(
        NORTHWIND,
        proposal_id,
        _timeout(arguments_hash),
        now=STARTED + APPROVAL_TTL,
    )

    assert expired.state == ApprovalProcessState.EXPIRED.value
    assert again.id == expired.id
    assert again.state == ApprovalProcessState.EXPIRED.value
    assert _proposal(db, proposal_id).status == ProposalStatus.EXPIRED.value
    assert _approval(db, proposal_id).status == ApprovalStatus.EXPIRED.value
    assert _orders(db, proposal_id) == []


def test_reject_leaves_no_draft_after_the_rejection_event(db: Session) -> None:
    proposal_id, run_id, arguments_hash = _waiting(db)
    _decide(db, proposal_id, "reject")
    assert _rejected_event(db, run_id, proposal_id)
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)

    rejected = machine.signal(NORTHWIND, proposal_id, _reject(arguments_hash), now=STARTED)
    again = machine.signal(NORTHWIND, proposal_id, _reject(arguments_hash), now=STARTED)

    assert rejected.state == ApprovalProcessState.REJECTED.value
    assert again.state == ApprovalProcessState.REJECTED.value
    assert _orders(db, proposal_id) == []
    assert _rejected_event(db, run_id, proposal_id)


def test_hash_mismatch_stops_before_the_draft(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal_id, _run_id, arguments_hash = _waiting(db)
    monkeypatch.setattr(ApprovalService, "_signal_wait", lambda *_args: None)
    _decide(db, proposal_id, "approve")
    proposal = _proposal(db, proposal_id)
    changed = dict(proposal.arguments)
    changed["quantity"] = 260
    proposal.arguments = changed
    proposal.estimated_impact = {"extended_cost_cents": 260 * UNIT_COST}
    db.commit()
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)

    stopped = machine.signal(NORTHWIND, proposal_id, _approve(arguments_hash), now=STARTED)

    assert stopped.state == ApprovalProcessState.APPROVED.value
    assert stopped.arguments_hash == arguments_hash
    assert _orders(db, proposal_id) == []


def test_wait_does_not_invoke_the_planner(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(self: object, request: object) -> object:
        raise AssertionError(request)

    monkeypatch.setattr(DeterministicPlanner, "plan", _boom)
    monkeypatch.setattr(ModelSupervisorPlanner, "plan", _boom)
    proposal_id, _run_id, arguments_hash = _waiting(db)
    _decide(db, proposal_id, "approve")
    machine = LocalApprovalProcess(db)
    machine.start(NORTHWIND, proposal_id)

    row = machine.signal(
        NORTHWIND,
        proposal_id,
        _approve(arguments_hash),
        now=STARTED,
    )

    assert row.state == ApprovalProcessState.NOTIFIED.value


def test_handlers_do_not_reach_a_model() -> None:
    machine = REPO_ROOT / "apps/api/opsmesh_api/workflows/approval_machine.py"
    handler_path = REPO_ROOT / "infra/functions/approval_process/handler.py"
    for path in (machine, handler_path):
        assert _imported_modules(path).isdisjoint(_MODEL_MODULES)
    cloud = REPO_ROOT / "apps/api/opsmesh_api/workflows/state_machine_cloud.py"
    imported = _imported_modules(cloud)
    assert "opsmesh_api.agents.planner" not in imported
    assert "opsmesh_api.agents.model_adapter" not in imported
    module = _load_handler(handler_path)
    result = module.handler({"action": "create_draft", "proposal_id": "po"}, None)
    assert result == {
        "ok": True,
        "action": "create_draft",
        "state": "draft_created",
        "proposal_id": "po",
    }
    assert module.handler({"action": "train"}, None)["ok"] is False


def test_definition_uses_the_approval_expiry() -> None:
    document = json.loads((REPO_ROOT / "workflows/approval_wait.asl.json").read_text())
    states = document["States"]
    for name in ("Waiting", "Approved", "Rejected", "Expired", "DraftCreated", "Notified"):
        assert name in states
    assert states["Waiting"]["TimeoutSeconds"] == int(APPROVAL_TTL.total_seconds())
    assert states["Rejected"]["Type"] == "Succeed"
    assert states["Approved"]["Default"] == "DraftCreated"
    assert "DraftCreated" not in states["Rejected"]


def test_local_profile_does_not_construct_a_state_machine_client(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_AGENTIC_FEATURES_ENABLED", raising=False)
    monkeypatch.delenv("CLOUD_STATE_MACHINE_ENABLED", raising=False)
    loaded = "boto3" in sys.modules
    settings = Settings(_env_file=None)
    machine = runner_for(db, settings)
    assert type(machine) is LocalApprovalProcess
    assert ("boto3" in sys.modules) is loaded


def test_requesting_an_approval_opens_the_wait(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)

    assert _wait(db, proposal_id).state == ApprovalProcessState.WAITING.value


def test_approving_through_the_service_creates_the_draft(db: Session) -> None:
    proposal_id, run_id, _arguments_hash = _waiting(db)

    _decide(db, proposal_id, "approve")

    assert _wait(db, proposal_id).state == ApprovalProcessState.NOTIFIED.value
    assert len(_orders(db, proposal_id)) == 1
    assert len(_notifications(db, run_id)) == 1


def test_rejecting_through_the_service_leaves_no_draft(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)

    _decide(db, proposal_id, "reject")

    assert _wait(db, proposal_id).state == ApprovalProcessState.REJECTED.value
    assert _orders(db, proposal_id) == []


def test_the_draft_waits_for_every_required_role(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(
        db,
        quantity=1000,
        roles=(ApprovalRole.PROCUREMENT_MANAGER, ApprovalRole.FINANCE_APPROVER),
    )

    _decide(db, proposal_id, "approve", ApprovalRole.PROCUREMENT_MANAGER)
    assert _wait(db, proposal_id).state == ApprovalProcessState.WAITING.value
    assert _orders(db, proposal_id) == []

    _decide(db, proposal_id, "approve", ApprovalRole.FINANCE_APPROVER)
    assert _wait(db, proposal_id).state == ApprovalProcessState.NOTIFIED.value
    assert len(_orders(db, proposal_id)) == 1


def test_reading_the_queue_after_expiry_delivers_the_timeout(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)
    service = ApprovalService(db)

    service.list_approvals(NORTHWIND, now=STARTED + APPROVAL_TTL - timedelta(seconds=1))
    assert _wait(db, proposal_id).state == ApprovalProcessState.WAITING.value

    queue = service.list_approvals(NORTHWIND, now=STARTED + APPROVAL_TTL)

    assert _wait(db, proposal_id).state == ApprovalProcessState.EXPIRED.value
    assert _proposal(db, proposal_id).status == ProposalStatus.EXPIRED.value
    assert [item.status for item in queue.approvals if item.proposal_id == proposal_id] == [
        ApprovalStatus.EXPIRED.value
    ]
    assert _orders(db, proposal_id) == []


def test_a_proposal_from_before_the_wait_still_gets_its_draft(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)
    db.execute(delete(ApprovalProcess).where(ApprovalProcess.proposal_id == proposal_id))
    db.commit()

    _decide(db, proposal_id, "approve")

    assert _wait(db, proposal_id).state == ApprovalProcessState.NOTIFIED.value
    assert len(_orders(db, proposal_id)) == 1


def test_managed_start_calls_step_functions(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)
    client = _Started()
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        cloud_state_machine_enabled=True,
        state_machine_arn="arn:aws:states:us-east-1:000000000000:stateMachine:approval-wait",
    )
    db.execute(delete(ApprovalProcess).where(ApprovalProcess.proposal_id == proposal_id))
    db.commit()
    machine = StepFunctionsApprovalProcess(db, settings, client=client)

    started = machine.start(NORTHWIND, proposal_id)
    machine.start(NORTHWIND, proposal_id)

    assert started.state == ApprovalProcessState.WAITING.value
    assert len(client.calls) == 1
    assert client.calls[0]["stateMachineArn"] == settings.state_machine_arn
    assert _orders(db, proposal_id) == []


def test_missing_state_machine_arn_fails_closed(db: Session) -> None:
    proposal_id, _run_id, _arguments_hash = _waiting(db)
    client = _Started()
    settings = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        cloud_state_machine_enabled=True,
        state_machine_arn="",
    )
    machine = StepFunctionsApprovalProcess(db, settings, client=client)

    with pytest.raises(ApprovalProcessError):
        machine.start(NORTHWIND, proposal_id)

    assert client.calls == []


def _waiting(
    db: Session,
    quantity: int = 250,
    roles: tuple[ApprovalRole, ...] = (ApprovalRole.PROCUREMENT_MANAGER,),
) -> tuple[UUID, UUID, str]:
    arguments = {
        "sku": SKU,
        "supplier_id": SUPPLIER,
        "quantity": quantity,
        "unit_cost_cents": UNIT_COST,
    }
    run_id = uuid4()
    proposal_id = uuid4()
    store = WorkflowStore(db)
    store.create_run(
        NORTHWIND,
        WorkflowRunCreate(
            id=run_id,
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status="RUNNING",
            input={"sku": SKU},
            started_at=STARTED,
        ),
    )
    store.add_proposal(
        NORTHWIND,
        run_id,
        ProposalCreate(
            id=proposal_id,
            proposal_type="reorder",
            arguments=arguments,
            estimated_impact={"extended_cost_cents": quantity * UNIT_COST},
            risk_level="low",
            status=ProposalStatus.PENDING_APPROVAL,
            created_at=STARTED,
        ),
    )
    for role in roles:
        store.add_approval(
            NORTHWIND,
            run_id,
            proposal_id,
            ApprovalCreate(
                id=uuid4(),
                arguments=arguments,
                required_role=role,
                status=ApprovalStatus.PENDING,
                expires_at=STARTED + APPROVAL_TTL,
            ),
        )
    return proposal_id, run_id, canonical_json_hash(arguments)


def _decide(
    db: Session,
    proposal_id: UUID,
    decision: str,
    role: ApprovalRole = ApprovalRole.PROCUREMENT_MANAGER,
) -> None:
    approval = db.scalar(
        select(Approval).where(
            Approval.proposal_id == proposal_id,
            Approval.required_role == role.value,
        )
    )
    assert approval is not None
    ApprovalService(db).decide(
        NORTHWIND,
        approval.id,
        Actor(f"{role.value}@northwind.example", frozenset({role.value})),
        decision=decision,
        comment=None,
        now=STARTED,
    )


def _wait(db: Session, proposal_id: UUID) -> ApprovalProcess:
    row = db.scalar(select(ApprovalProcess).where(ApprovalProcess.proposal_id == proposal_id))
    assert row is not None
    db.refresh(row)
    return row


def _approve(arguments_hash: str) -> ProcessSignal:
    return ProcessSignal(ProcessSignalKind.APPROVE, arguments_hash)


def _reject(arguments_hash: str) -> ProcessSignal:
    return ProcessSignal(ProcessSignalKind.REJECT, arguments_hash)


def _timeout(arguments_hash: str) -> ProcessSignal:
    return ProcessSignal(ProcessSignalKind.TIMEOUT, arguments_hash)


def _orders(db: Session, proposal_id: UUID) -> list[PurchaseOrder]:
    return list(
        db.scalars(select(PurchaseOrder).where(PurchaseOrder.proposal_id == proposal_id)).all()
    )


def _notifications(db: Session, run_id: UUID) -> list[NotificationInbox]:
    return list(
        db.scalars(
            select(NotificationInbox).where(
                NotificationInbox.workflow_id == run_id,
                NotificationInbox.event_type == EventType.DRAFT_PO_CREATED.value,
            )
        ).all()
    )


def _proposal(db: Session, proposal_id: UUID) -> OperationalProposal:
    proposal = db.get(OperationalProposal, proposal_id)
    assert proposal is not None
    return proposal


def _approval(db: Session, proposal_id: UUID) -> Approval:
    approval = db.scalar(select(Approval).where(Approval.proposal_id == proposal_id))
    assert approval is not None
    return approval


def _rejected_event(db: Session, run_id: UUID, proposal_id: UUID) -> bool:
    rows = db.scalars(
        select(OutboxRow).where(
            OutboxRow.workflow_id == run_id,
            OutboxRow.event_type == EventType.APPROVAL_REJECTED.value,
        )
    ).all()
    return any(str(row.payload.get("proposal_id")) == str(proposal_id) for row in rows)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def _load_handler(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("approval_process_handler", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
