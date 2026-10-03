"""Human approval is bound to the proposal hash and required before submit."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from httpx import Response
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.services.models import PurchaseOrder, PurchaseOrderStatus
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.tools.schemas import CREATE_DRAFT_TOOL, SUBMIT_FOR_APPROVAL_TOOL
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import (
    Approval,
    ApprovalStatus,
    AuditRecord,
    OperationalProposal,
    ProposalStatus,
)
from opsmesh_api.workflows.schemas import ApprovalCreate
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tests.auth_tokens import bearer
from tests.test_draft_procurement import (
    CONTOSO,
    NORTHWIND,
    UNIT_COST,
    _add_proposal,
    _call,
    _draft_arguments,
    _executor_task,
    _reorder,
    _run,
    _stored_proposal,
)

MANAGER = "manager@northwind.example"
FINANCE = "finance@northwind.example"
VIEWER = "viewer@northwind.example"
ANALYST = "analyst@northwind.example"
ADMIN = "admin@northwind.example"


def _headers(actor: str, role: str) -> dict[str, str]:
    return bearer(actor, role)


def _golden(client: TestClient) -> tuple[str, str, str]:
    body = _reorder(client)
    run_id = str(body["run_id"])
    proposal = _stored_proposal(client, run_id)
    approvals = proposal["approvals"]
    assert isinstance(approvals, list)
    assert len(approvals) == 1
    approval = approvals[0]
    assert isinstance(approval, dict)
    return run_id, str(proposal["id"]), str(approval["id"])


def _draft(
    session_factory: sessionmaker[Session],
    run_id: str,
    proposal_id: str,
    *,
    key: str,
) -> str:
    with session_factory() as db:
        task_id = _executor_task(db, NORTHWIND, UUID(run_id))
        created = LocalToolRegistry(db).invoke(
            _call(
                NORTHWIND,
                UUID(run_id),
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(UUID(proposal_id), idempotency_key=key),
            )
        )
        assert created.data is not None
        return str(created.data["purchase_order_id"])


def _submit(
    session_factory: sessionmaker[Session],
    run_id: str,
    order_id: str,
    *,
    key: str,
) -> tuple[str, str | None]:
    with session_factory() as db:
        task_id = _executor_task(db, NORTHWIND, UUID(run_id))
        result = LocalToolRegistry(db).invoke(
            _call(
                NORTHWIND,
                UUID(run_id),
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                SUBMIT_FOR_APPROVAL_TOOL,
                {"purchase_order_id": order_id, "idempotency_key": key},
            )
        )
        status = result.status.value
        if result.status.value == "FAILED":
            return status, result.error_code
        assert result.data is not None
        return str(result.data["status"]), None


def _decide(
    client: TestClient,
    approval_id: str,
    actor: str,
    role: str,
    *,
    decision: str = "approve",
    comment: str | None = None,
) -> Response:
    body: dict[str, str] = {"decision": decision}
    if comment is not None:
        body["comment"] = comment
    return client.post(
        f"/tenants/{NORTHWIND}/approvals/{approval_id}/decision",
        headers=_headers(actor, role),
        json=body,
    )


def test_manager_approval_matches_the_proposal_hash(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _run_id, proposal_id, approval_id = _golden(client)
    denied_viewer = _decide(client, approval_id, VIEWER, "viewer")
    denied_analyst = _decide(client, approval_id, ANALYST, "operations_analyst")
    assert denied_viewer.status_code == 403
    assert denied_analyst.status_code == 403
    pending = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{_run_id}")
    assert pending.json()["proposals"][0]["status"] == "PENDING_APPROVAL"
    assert pending.json()["proposals"][0]["approvals"][0]["status"] == "PENDING"

    approved = _decide(client, approval_id, MANAGER, "procurement_manager")
    assert approved.status_code == 200
    payload = approved.json()
    proposal = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{_run_id}").json()["proposals"][0]
    live_hash = canonical_json_hash(proposal["arguments"])
    assert payload["status"] == "APPROVED"
    assert payload["approved_by"] == MANAGER
    assert payload["arguments_hash"] == live_hash
    assert payload["required_role"] == "procurement_manager"
    with session_factory() as db:
        audit = db.scalar(
            select(AuditRecord).where(
                AuditRecord.proposal_id == UUID(proposal_id),
                AuditRecord.action == "approval.approved",
            )
        )
        assert audit is not None
        assert audit.actor == MANAGER
        assert audit.arguments_hash == live_hash
        assert audit.proposal_id == UUID(proposal_id)


def test_viewer_cannot_submit_until_the_manager_approves_the_same_hash(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    run_id, proposal_id, approval_id = _golden(client)
    order_id = _draft(session_factory, run_id, proposal_id, key="golden-approval-draft")
    assert _decide(client, approval_id, VIEWER, "viewer").status_code == 403
    status, code = _submit(session_factory, run_id, order_id, key="golden-submit-blocked")
    assert status == "FAILED"
    assert code == "approval_required"
    with session_factory() as db:
        order = db.get(PurchaseOrder, order_id)
        assert order is not None
        assert order.status == PurchaseOrderStatus.DRAFT.value

    approved = _decide(client, approval_id, MANAGER, "procurement_manager")
    assert approved.status_code == 200
    status, code = _submit(session_factory, run_id, order_id, key="golden-submit-allowed")
    assert code is None
    assert status == "submitted"
    with session_factory() as db:
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        assert proposal.status == ProposalStatus.APPROVED.value
        live_hash = canonical_json_hash(proposal.arguments)
        audit = db.scalar(
            select(AuditRecord).where(
                AuditRecord.proposal_id == proposal.id,
                AuditRecord.action == "procurement.submitted",
            )
        )
        assert audit is not None
        assert audit.actor == MANAGER
        assert audit.arguments_hash == live_hash


def test_a_changed_quantity_requires_a_new_approval(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    run_id, proposal_id, approval_id = _golden(client)
    order_id = _draft(session_factory, run_id, proposal_id, key="edited-draft")
    assert _decide(client, approval_id, MANAGER, "procurement_manager").status_code == 200
    with session_factory() as db:
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        proposal.arguments = {**proposal.arguments, "quantity": 300}
        db.commit()
        live_hash = canonical_json_hash(proposal.arguments)
    status, code = _submit(session_factory, run_id, order_id, key="edited-submit")
    assert status == "FAILED"
    assert code == "approval_hash_mismatch"
    stale = _decide(client, approval_id, MANAGER, "procurement_manager")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "approval_conflict"
    with session_factory() as db:
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        fresh_id = uuid4()
        WorkflowStore(db).add_approval(
            NORTHWIND,
            UUID(run_id),
            UUID(proposal_id),
            ApprovalCreate(
                id=fresh_id,
                arguments=dict(proposal.arguments),
                required_role="procurement_manager",
                status="PENDING",
                expires_at=datetime.now(UTC) + timedelta(hours=72),
            ),
        )
    assert _decide(client, str(fresh_id), MANAGER, "procurement_manager").status_code == 200
    status, code = _submit(session_factory, run_id, order_id, key="edited-submit-again")
    assert code is None
    assert status == "submitted"
    with session_factory() as db:
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        approved = db.get(Approval, fresh_id)
        assert approved is not None
        assert approved.arguments_hash == canonical_json_hash(proposal.arguments)
        assert approved.arguments_hash == live_hash


def test_finance_and_manager_approvals_are_independent(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=1000,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        arguments = db.get(OperationalProposal, proposal_id)
        assert arguments is not None
        stored_arguments = {
            **arguments.arguments,
            "extended_cost_cents": 1000 * UNIT_COST,
        }
        arguments.arguments = stored_arguments
        arguments.estimated_impact = {"extended_cost_cents": 1000 * UNIT_COST}
        db.commit()
        expires = datetime.now(UTC) + timedelta(hours=72)
        manager_id = uuid4()
        finance_id = uuid4()
        store = WorkflowStore(db)
        for approval_id, role in (
            (manager_id, "procurement_manager"),
            (finance_id, "finance_approver"),
        ):
            store.add_approval(
                NORTHWIND,
                run_id,
                proposal_id,
                ApprovalCreate(
                    id=approval_id,
                    arguments=stored_arguments,
                    required_role=role,  # type: ignore[arg-type]
                    status="PENDING",
                    expires_at=expires,
                ),
            )
        task_id = _executor_task(db, NORTHWIND, run_id)
        created = LocalToolRegistry(db).invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(
                    proposal_id,
                    quantity=1000,
                    unit_cost_cents=UNIT_COST,
                    idempotency_key="finance-draft",
                ),
            )
        )
        assert created.data is not None
        order_id = str(created.data["purchase_order_id"])

    assert _decide(client, str(manager_id), MANAGER, "procurement_manager").status_code == 200
    status, code = _submit(session_factory, str(run_id), order_id, key="finance-submit-manager")
    assert status == "FAILED"
    assert code == "approval_required"
    assert _decide(client, str(finance_id), MANAGER, "procurement_manager").status_code == 403
    assert _decide(client, str(finance_id), FINANCE, "finance_approver").status_code == 200
    status, code = _submit(session_factory, str(run_id), order_id, key="finance-submit-both")
    assert code is None
    assert status == "submitted"


def test_a_pending_decision_refuses_a_changed_proposal(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _run_id, proposal_id, approval_id = _golden(client)
    with session_factory() as db:
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        proposal.arguments = {**proposal.arguments, "quantity": 200}
        db.commit()
    stale = _decide(client, approval_id, MANAGER, "procurement_manager")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "approval_hash_mismatch"
    with session_factory() as db:
        approval = db.get(Approval, UUID(approval_id))
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING.value


def test_rejection_denies_submit(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    run_id, proposal_id, approval_id = _golden(client)
    order_id = _draft(session_factory, run_id, proposal_id, key="reject-draft")
    rejected = _decide(
        client,
        approval_id,
        MANAGER,
        "procurement_manager",
        decision="reject",
        comment="Quantity is higher than the shelf plan.",
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    status, code = _submit(session_factory, run_id, order_id, key="reject-submit")
    assert status == "FAILED"
    assert code == "approval_rejected"
    with session_factory() as db:
        audit = db.scalar(
            select(AuditRecord).where(
                AuditRecord.approval_id == UUID(approval_id),
                AuditRecord.action == "approval.rejected",
            )
        )
        assert audit is not None
        assert audit.actor == MANAGER
        assert audit.detail == "Quantity is higher than the shelf plan."
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        assert proposal is not None
        assert proposal.status == ProposalStatus.REJECTED.value


def test_an_expired_pending_approval_blocks_submit(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    run_id, proposal_id, approval_id = _golden(client)
    order_id = _draft(session_factory, run_id, proposal_id, key="expired-draft")
    with session_factory() as db:
        approval = db.get(Approval, UUID(approval_id))
        assert approval is not None
        approval.expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    status, code = _submit(session_factory, run_id, order_id, key="expired-submit")
    assert status == "FAILED"
    assert code == "approval_expired"
    with session_factory() as db:
        approval = db.get(Approval, UUID(approval_id))
        proposal = db.get(OperationalProposal, UUID(proposal_id))
        order = db.get(PurchaseOrder, order_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.EXPIRED.value
        assert proposal is not None
        assert proposal.status == ProposalStatus.EXPIRED.value
        assert order is not None
        assert order.status == PurchaseOrderStatus.DRAFT.value


def test_a_second_decision_conflicts(client: TestClient) -> None:
    _run_id, _proposal_id, approval_id = _golden(client)
    first = _decide(client, approval_id, MANAGER, "procurement_manager")
    second = _decide(client, approval_id, "other.manager@northwind.example", "procurement_manager")
    assert first.status_code == 200
    assert second.status_code == 409
    body = second.json()
    assert body["detail"]["code"] == "approval_conflict"
    assert MANAGER in body["detail"]["message"]
    assert first.json()["approved_by"] == MANAGER


def test_admin_cannot_skip_the_role_or_the_hash(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    run_id, proposal_id, approval_id = _golden(client)
    order_id = _draft(session_factory, run_id, proposal_id, key="admin-draft")
    denied = _decide(client, approval_id, ADMIN, "admin")
    assert denied.status_code == 403
    assert denied.json()["detail"]["message"] == "Requires procurement_manager role."
    missing = client.post(
        f"/tenants/{NORTHWIND}/approvals/{approval_id}/decision",
        headers={"Authorization": ""},
        json={"decision": "approve"},
    )
    assert missing.status_code == 401
    status, code = _submit(session_factory, run_id, order_id, key="admin-submit")
    assert status == "FAILED"
    assert code == "approval_required"
    with session_factory() as db:
        approval = db.get(Approval, UUID(approval_id))
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING.value
        assert approval.approved_by is None


def test_append_endpoint_cannot_record_a_terminal_decision(
    client: TestClient,
) -> None:
    run_id, proposal_id, _approval_id = _golden(client)
    response = client.post(
        f"/tenants/{NORTHWIND}/workflow-runs/{run_id}/proposals/{proposal_id}/approvals",
        json={
            "id": str(uuid4()),
            "arguments": {"quantity": 250},
            "required_role": "procurement_manager",
            "status": "APPROVED",
            "expires_at": (datetime.now(UTC) + timedelta(hours=72)).isoformat(),
            "approved_by": ADMIN,
        },
    )
    assert response.status_code == 422
    assert "pending" in response.json()["detail"].casefold()


def test_queue_is_tenant_scoped(client: TestClient) -> None:
    _run_id, _proposal_id, approval_id = _golden(client)
    listed = client.get(f"/tenants/{NORTHWIND}/approvals")
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["approvals"]}
    assert approval_id in ids
    other = client.get(f"/tenants/{CONTOSO}/approvals/{approval_id}")
    assert other.status_code == 404
    foreign = client.post(
        f"/tenants/{CONTOSO}/approvals/{approval_id}/decision",
        headers=_headers(MANAGER, "procurement_manager"),
        json={"decision": "approve"},
    )
    assert foreign.status_code == 404


def test_reject_without_a_comment_is_refused(client: TestClient) -> None:
    _run_id, _proposal_id, approval_id = _golden(client)
    response = _decide(
        client,
        approval_id,
        MANAGER,
        "procurement_manager",
        decision="reject",
        comment="   ",
    )
    assert response.status_code == 422
