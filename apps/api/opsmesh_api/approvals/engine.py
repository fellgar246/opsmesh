"""Decide an approval and tell submit whether that decision still applies."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from opsmesh_api.approvals.errors import ApprovalError
from opsmesh_api.approvals.schemas import ApprovalDetail, ApprovalQueue, ApprovalView
from opsmesh_api.events.dispatch import publish_ready
from opsmesh_api.events.recording import record_approval_decision
from opsmesh_api.identity.access import allow
from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import Tenant
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import (
    Approval,
    ApprovalRole,
    ApprovalStatus,
    AuditRecord,
    OperationalProposal,
    ProposalStatus,
    WorkflowRun,
)

APPROVAL_TTL = timedelta(hours=72)

_ACTION_APPROVED = "approval.approved"
_ACTION_REJECTED = "approval.rejected"
_ACTION_SUBMITTED = "procurement.submitted"


@dataclass(frozen=True)
class Actor:
    """Authenticated user. Roles come from verified claims."""

    actor: str
    roles: frozenset[str]


@dataclass(frozen=True)
class SubmitAssessment:
    """Whether submit may advance a draft, park a proposal that needs no approval, or stop."""

    allowed: bool
    advance: bool
    code: str
    message: str
    expire_ids: tuple[UUID, ...]


def proposal_extended_cost(proposal: OperationalProposal) -> int:
    """Extended cost in cents from the stored proposal. Missing money is zero."""

    direct = _int_field(proposal.arguments, "extended_cost_cents")
    if direct is not None:
        return direct
    impact = _int_field(proposal.estimated_impact, "extended_cost_cents")
    if impact is not None:
        return impact
    quantity = _int_field(proposal.arguments, "quantity")
    unit_cost = _int_field(proposal.arguments, "unit_cost_cents")
    if quantity is not None and unit_cost is not None:
        return quantity * unit_cost
    return 0


def required_roles(extended_cost_cents: int) -> tuple[str, ...]:
    """Roles the policy engine requires for this total. Admin is never a substitute."""

    requires_manager, requires_finance = approval_for_total(extended_cost_cents)
    roles: list[str] = []
    if requires_manager:
        roles.append(ApprovalRole.PROCUREMENT_MANAGER.value)
    if requires_finance:
        roles.append(ApprovalRole.FINANCE_APPROVER.value)
    return tuple(roles)


def assess_submit(
    db: Session,
    proposal: OperationalProposal,
    now: datetime | None = None,
) -> SubmitAssessment:
    """Compare each required approval with the live proposal hash."""

    moment = now or datetime.now(UTC)
    needed = required_roles(proposal_extended_cost(proposal))
    rows = _proposal_approvals(db, proposal.tenant_id, proposal.id)
    expire_ids = tuple(
        row.id
        for row in rows
        if row.status == ApprovalStatus.PENDING.value and row.expires_at <= moment
    )
    if not needed:
        return SubmitAssessment(True, False, "ok", "", expire_ids)
    live_hash = canonical_json_hash(proposal.arguments)
    unmet: list[str] = []
    for role in needed:
        matching = [row for row in rows if row.required_role == role]
        if any(
            row.status == ApprovalStatus.APPROVED.value and row.arguments_hash == live_hash
            for row in matching
        ):
            continue
        if any(
            row.status == ApprovalStatus.REJECTED.value and row.arguments_hash == live_hash
            for row in matching
        ):
            return SubmitAssessment(
                False,
                False,
                "approval_rejected",
                "Submit is denied because the proposal was rejected.",
                expire_ids,
            )
        pending_live = [
            row
            for row in matching
            if row.status == ApprovalStatus.PENDING.value and row.arguments_hash == live_hash
        ]
        if pending_live and all(row.expires_at <= moment for row in pending_live):
            return SubmitAssessment(
                False,
                False,
                "approval_expired",
                "The approval expired.",
                expire_ids,
            )
        if any(
            row.status == ApprovalStatus.APPROVED.value and row.arguments_hash != live_hash
            for row in matching
        ):
            return SubmitAssessment(
                False,
                False,
                "approval_hash_mismatch",
                "The approval does not match the proposal arguments.",
                expire_ids,
            )
        unmet.append(role)
    if unmet:
        return SubmitAssessment(
            False,
            False,
            "approval_required",
            f"Submit requires {unmet[0]} approval for the same arguments.",
            expire_ids,
        )
    return SubmitAssessment(True, True, "ok", "", expire_ids)


def expire_approvals(db: Session, approval_ids: tuple[UUID, ...]) -> None:
    """Mark past-due pending rows expired. The caller commits."""

    if not approval_ids:
        return
    rows = db.scalars(select(Approval).where(Approval.id.in_(approval_ids))).all()
    for row in rows:
        if row.status == ApprovalStatus.PENDING.value:
            row.status = ApprovalStatus.EXPIRED.value


def audit_submission(
    db: Session,
    proposal: OperationalProposal,
    now: datetime | None = None,
) -> None:
    """Record each matching approval used when a draft advances."""

    moment = now or datetime.now(UTC)
    live_hash = canonical_json_hash(proposal.arguments)
    needed = set(required_roles(proposal_extended_cost(proposal)))
    for row in _proposal_approvals(db, proposal.tenant_id, proposal.id):
        if row.required_role not in needed:
            continue
        if row.status != ApprovalStatus.APPROVED.value or row.arguments_hash != live_hash:
            continue
        append_audit(
            db,
            tenant_id=proposal.tenant_id,
            actor=row.approved_by or "unknown",
            action=_ACTION_SUBMITTED,
            proposal_id=proposal.id,
            arguments_hash=live_hash,
            approval_id=row.id,
            detail=None,
            occurred_at=moment,
        )


def append_audit(
    db: Session,
    *,
    tenant_id: str,
    actor: str,
    action: str,
    proposal_id: UUID,
    arguments_hash: str,
    approval_id: UUID | None,
    detail: str | None,
    occurred_at: datetime | None = None,
) -> None:
    db.add(
        AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            actor=actor,
            occurred_at=occurred_at or datetime.now(UTC),
            action=action,
            proposal_id=proposal_id,
            arguments_hash=arguments_hash,
            approval_id=approval_id,
            detail=detail,
        )
    )


class ApprovalService:
    """List and decide approvals for one tenant. Submit uses the same hash check."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_approvals(self, tenant_id: str) -> ApprovalQueue:
        self._require_tenant(tenant_id)
        rows = self._joined(tenant_id)
        return ApprovalQueue(
            approvals=[_view(approval, proposal, run) for approval, proposal, run in rows]
        )

    def get_approval(self, tenant_id: str, approval_id: UUID) -> ApprovalDetail:
        self._require_tenant(tenant_id)
        found = self._one(tenant_id, approval_id)
        if found is None:
            raise NotFoundError()
        approval, proposal, run = found
        siblings = [
            _view(item, proposal, run)
            for item in _proposal_approvals(self._db, tenant_id, proposal.id)
        ]
        view = _view(approval, proposal, run)
        return ApprovalDetail(**view.model_dump(), proposal_approvals=siblings)

    def decide(
        self,
        tenant_id: str,
        approval_id: UUID,
        actor: Actor,
        *,
        decision: str,
        comment: str | None,
        now: datetime | None = None,
    ) -> ApprovalDetail:
        """Record one terminal decision. A second decision for the same row conflicts."""

        moment = now or datetime.now(UTC)
        self._require_tenant(tenant_id)
        found = self._one(tenant_id, approval_id)
        if found is None:
            raise NotFoundError()
        approval, proposal, run = found
        if proposal.status == ProposalStatus.SUPERSEDED.value:
            raise ApprovalError(422, "proposal_inactive", "The proposal is no longer active.")
        if approval.status != ApprovalStatus.PENDING.value:
            raise self._conflict(approval)
        if not allow(
            user=approval.required_role in actor.roles,
            agent=True,
            tool=True,
            policy=True,
        ):
            raise ApprovalError(
                403,
                "role_denied",
                f"Requires {approval.required_role} role.",
            )
        if approval.expires_at <= moment:
            approval.status = ApprovalStatus.EXPIRED.value
            self._db.commit()
            publish_ready(self._db)
            raise ApprovalError(422, "approval_expired", "The approval expired.")
        live_hash = canonical_json_hash(proposal.arguments)
        if approval.arguments_hash != live_hash:
            raise ApprovalError(
                409,
                "approval_hash_mismatch",
                "Proposal changed after this approval was requested. A new approval is required.",
            )
        status = (
            ApprovalStatus.APPROVED.value
            if decision == "approve"
            else ApprovalStatus.REJECTED.value
        )
        result = self._db.execute(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.tenant_id == tenant_id,
                Approval.status == ApprovalStatus.PENDING.value,
            )
            .values(status=status, approved_by=actor.actor)
            .execution_options(synchronize_session="fetch")
        )
        changed = result.rowcount if isinstance(result, CursorResult) else 0
        if changed != 1:
            self._db.rollback()
            current = self._db.get(Approval, approval_id)
            if current is None:
                raise NotFoundError()
            raise self._conflict(current)
        append_audit(
            self._db,
            tenant_id=tenant_id,
            actor=actor.actor,
            action=_ACTION_APPROVED if decision == "approve" else _ACTION_REJECTED,
            proposal_id=proposal.id,
            arguments_hash=live_hash,
            approval_id=approval.id,
            detail=comment if decision == "reject" else None,
            occurred_at=moment,
        )
        record_approval_decision(
            self._db,
            workflow_id=run.id,
            approval=approval,
            actor=actor.actor,
            granted=decision == "approve",
            occurred_at=moment,
        )
        self._sync_proposal(proposal)
        self._db.commit()
        publish_ready(self._db)
        return self.get_approval(tenant_id, approval_id)

    def _sync_proposal(self, proposal: OperationalProposal) -> None:
        needed = required_roles(proposal_extended_cost(proposal))
        if not needed:
            return
        rows = _proposal_approvals(self._db, proposal.tenant_id, proposal.id)
        live_hash = canonical_json_hash(proposal.arguments)
        if any(
            row.required_role in needed
            and row.status == ApprovalStatus.REJECTED.value
            and row.arguments_hash == live_hash
            for row in rows
        ):
            proposal.status = ProposalStatus.REJECTED.value
            return
        satisfied = all(
            any(
                row.required_role == role
                and row.status == ApprovalStatus.APPROVED.value
                and row.arguments_hash == live_hash
                for row in rows
            )
            for role in needed
        )
        if satisfied:
            proposal.status = ProposalStatus.APPROVED.value

    def _conflict(self, approval: Approval) -> ApprovalError:
        audit = self._db.scalar(
            select(AuditRecord)
            .where(
                AuditRecord.tenant_id == approval.tenant_id,
                AuditRecord.approval_id == approval.id,
                AuditRecord.action.in_((_ACTION_APPROVED, _ACTION_REJECTED)),
            )
            .order_by(AuditRecord.occurred_at.desc())
        )
        if approval.status == ApprovalStatus.EXPIRED.value:
            return ApprovalError(
                409,
                "approval_conflict",
                "This approval expired.",
                status=approval.status,
            )
        actor = approval.approved_by or "another approver"
        when = audit.occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC") if audit is not None else None
        verb = "approved" if approval.status == ApprovalStatus.APPROVED.value else "rejected"
        when_text = f" at {when}" if when else ""
        return ApprovalError(
            409,
            "approval_conflict",
            f"This proposal was already {verb} by {actor}{when_text}.",
            status=approval.status,
            actor=approval.approved_by,
            occurred_at=None if audit is None else audit.occurred_at.isoformat(),
        )

    def _require_tenant(self, tenant_id: str) -> None:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()

    def _one(
        self,
        tenant_id: str,
        approval_id: UUID,
    ) -> tuple[Approval, OperationalProposal, WorkflowRun] | None:
        return next(
            (
                item
                for item in self._joined(tenant_id)
                if item[0].id == approval_id
            ),
            None,
        )

    def _joined(
        self,
        tenant_id: str,
    ) -> list[tuple[Approval, OperationalProposal, WorkflowRun]]:
        rows = self._db.execute(
            select(Approval, OperationalProposal, WorkflowRun)
            .join(OperationalProposal, OperationalProposal.id == Approval.proposal_id)
            .join(WorkflowRun, WorkflowRun.id == OperationalProposal.workflow_run_id)
            .where(
                Approval.tenant_id == tenant_id,
                OperationalProposal.tenant_id == tenant_id,
                WorkflowRun.tenant_id == tenant_id,
            )
            .order_by(Approval.expires_at, Approval.id)
        ).all()
        return [(approval, proposal, run) for approval, proposal, run in rows]


def _proposal_approvals(db: Session, tenant_id: str, proposal_id: UUID) -> list[Approval]:
    return list(
        db.scalars(
            select(Approval)
            .where(Approval.tenant_id == tenant_id, Approval.proposal_id == proposal_id)
            .order_by(Approval.id)
        ).all()
    )


def _view(approval: Approval, proposal: OperationalProposal, run: WorkflowRun) -> ApprovalView:
    return ApprovalView(
        id=approval.id,
        tenant_id=approval.tenant_id,
        proposal_id=proposal.id,
        workflow_run_id=run.id,
        arguments_hash=approval.arguments_hash,
        live_arguments_hash=canonical_json_hash(proposal.arguments),
        required_role=approval.required_role,  # type: ignore[arg-type]
        status=approval.status,  # type: ignore[arg-type]
        expires_at=approval.expires_at,
        approved_by=approval.approved_by,
        proposal_status=proposal.status,  # type: ignore[arg-type]
        quantity=_int_field(proposal.arguments, "quantity"),
        sku=_str_field(proposal.arguments, "sku"),
        supplier_id=_str_field(proposal.arguments, "supplier_id"),
        extended_cost_cents=proposal_extended_cost(proposal),
        requested_at=proposal.created_at,
        workflow_type=run.workflow_type,
        requested_by=run.requested_by,
    )


def _int_field(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if type(value) is int:
        return value
    return None


def _str_field(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    return None
