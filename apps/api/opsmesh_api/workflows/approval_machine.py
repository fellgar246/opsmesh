"""Deterministic approval wait.

Agent planning finishes before a proposal exists. This machine calls the
approval service and the draft service, then the notification consumer runs
on the local bus. The wait ends at the approval row's expiry.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.approvals.engine import decision_recorded, expire_approvals
from opsmesh_api.events.dispatch import publish_ready
from opsmesh_api.events.models import NotificationInbox, OutboxRow
from opsmesh_api.events.schemas import EventType
from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import PurchaseOrder
from opsmesh_api.services.procurement import ProcurementService
from opsmesh_api.settings import Settings, get_settings
from opsmesh_api.tools.schemas import DraftPurchaseOrderArgs, validated_arguments_hash
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import (
    Approval,
    ApprovalProcess,
    ApprovalProcessState,
    ApprovalStatus,
    OperationalProposal,
    ProcessSignalKind,
    ProposalStatus,
)

_TERMINAL = frozenset(
    {
        ApprovalProcessState.REJECTED.value,
        ApprovalProcessState.EXPIRED.value,
        ApprovalProcessState.NOTIFIED.value,
    }
)


class ApprovalProcessError(Exception):
    """The wait could not accept the signal."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ProcessSignal:
    """Approve, reject, or timeout, with the arguments hash the signal names."""

    kind: ProcessSignalKind
    arguments_hash: str


class ApprovalWait(Protocol):
    """Local machine or the managed state machine."""

    def start(self, tenant_id: str, proposal_id: UUID) -> ApprovalProcess:
        """Open the wait for a proposal that already exists."""

    def signal(
        self,
        tenant_id: str,
        proposal_id: UUID,
        signal: ProcessSignal,
        now: datetime | None = None,
    ) -> ApprovalProcess:
        """Apply one signal. A repeated signal keeps the current state."""


class LocalApprovalProcess:
    """In-process wait. Signals call the approval and draft services."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def start(self, tenant_id: str, proposal_id: UUID) -> ApprovalProcess:
        existing = self._process(tenant_id, proposal_id)
        if existing is not None:
            return existing
        proposal = self._proposal(tenant_id, proposal_id)
        if proposal is None:
            raise NotFoundError()
        row = ApprovalProcess(
            id=uuid4(),
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            state=ApprovalProcessState.WAITING.value,
            last_signal=None,
            arguments_hash=None,
            updated_at=datetime.now(UTC),
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def signal(
        self,
        tenant_id: str,
        proposal_id: UUID,
        signal: ProcessSignal,
        now: datetime | None = None,
    ) -> ApprovalProcess:
        row = self._process(tenant_id, proposal_id)
        if row is None:
            raise NotFoundError()
        moment = now or datetime.now(UTC)
        if signal.kind is ProcessSignalKind.APPROVE:
            return self._approve(row, signal, moment)
        if signal.kind is ProcessSignalKind.REJECT:
            return self._reject(row, signal, moment)
        return self._timeout(row, signal, moment)

    def _approve(
        self,
        row: ApprovalProcess,
        signal: ProcessSignal,
        moment: datetime,
    ) -> ApprovalProcess:
        if row.state == ApprovalProcessState.NOTIFIED.value:
            return row
        if row.state in _TERMINAL:
            return row
        proposal = self._require_proposal(row)
        if row.state == ApprovalProcessState.DRAFT_CREATED.value:
            return self._notify(row, proposal, signal, moment)
        granted = decision_recorded(
            self._db,
            proposal,
            arguments_hash=signal.arguments_hash,
            status=ApprovalStatus.APPROVED,
        )
        if not granted:
            return row
        if row.state == ApprovalProcessState.WAITING.value:
            self._move(row, ApprovalProcessState.APPROVED, signal, moment)
            self._db.commit()
            proposal = self._require_proposal(row)
        live_hash = canonical_json_hash(proposal.arguments)
        if live_hash != signal.arguments_hash:
            return self._reload(row)
        if not self._create_draft(proposal):
            return self._reload(row)
        proposal = self._require_proposal(row)
        self._move(row, ApprovalProcessState.DRAFT_CREATED, signal, moment)
        self._db.commit()
        return self._notify(row, proposal, signal, moment)

    def _reject(
        self,
        row: ApprovalProcess,
        signal: ProcessSignal,
        moment: datetime,
    ) -> ApprovalProcess:
        if row.state != ApprovalProcessState.WAITING.value:
            return row
        proposal = self._require_proposal(row)
        rejected = decision_recorded(
            self._db,
            proposal,
            arguments_hash=signal.arguments_hash,
            status=ApprovalStatus.REJECTED,
        )
        if not rejected or not self._rejection_event(proposal):
            return row
        self._move(row, ApprovalProcessState.REJECTED, signal, moment)
        self._db.commit()
        self._db.refresh(row)
        return row

    def _timeout(
        self,
        row: ApprovalProcess,
        signal: ProcessSignal,
        moment: datetime,
    ) -> ApprovalProcess:
        if row.state != ApprovalProcessState.WAITING.value:
            return row
        proposal = self._require_proposal(row)
        pending = self._db.scalars(
            select(Approval).where(
                Approval.tenant_id == proposal.tenant_id,
                Approval.proposal_id == proposal.id,
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at <= moment,
            )
        ).all()
        if not pending:
            return row
        expire_approvals(self._db, tuple(item.id for item in pending))
        proposal.status = ProposalStatus.EXPIRED.value
        self._move(row, ApprovalProcessState.EXPIRED, signal, moment)
        self._db.commit()
        self._db.refresh(row)
        return row

    def _notify(
        self,
        row: ApprovalProcess,
        proposal: OperationalProposal,
        signal: ProcessSignal,
        moment: datetime,
    ) -> ApprovalProcess:
        publish_ready(self._db)
        recorded = self._db.scalar(
            select(NotificationInbox.id).where(
                NotificationInbox.tenant_id == proposal.tenant_id,
                NotificationInbox.workflow_id == proposal.workflow_run_id,
                NotificationInbox.event_type == EventType.DRAFT_PO_CREATED.value,
            )
        )
        if recorded is None:
            return row
        self._move(row, ApprovalProcessState.NOTIFIED, signal, moment)
        self._db.commit()
        self._db.refresh(row)
        return row

    def _create_draft(self, proposal: OperationalProposal) -> bool:
        existing = self._db.scalar(
            select(PurchaseOrder.purchase_order_id).where(
                PurchaseOrder.tenant_id == proposal.tenant_id,
                PurchaseOrder.proposal_id == proposal.id,
            )
        )
        if existing is not None:
            return True
        commercial = _commercial(proposal.arguments)
        if commercial is None:
            return False
        sku, supplier_id, quantity, unit_cost_cents = commercial
        parsed = DraftPurchaseOrderArgs(
            proposal_id=proposal.id,
            sku=sku,
            supplier_id=supplier_id,
            quantity=quantity,
            unit_cost_cents=unit_cost_cents,
            idempotency_key=f"approval-process-{proposal.id}",
        )
        ProcurementService(self._db).create_draft_purchase_order(
            proposal.tenant_id,
            proposal_id=proposal.id,
            sku=sku,
            supplier_id=supplier_id,
            quantity=quantity,
            unit_cost_cents=unit_cost_cents,
            idempotency_key=parsed.idempotency_key,
            arguments_hash=validated_arguments_hash(parsed),
        )
        return True

    def _rejection_event(self, proposal: OperationalProposal) -> bool:
        rows = self._db.scalars(
            select(OutboxRow).where(
                OutboxRow.tenant_id == proposal.tenant_id,
                OutboxRow.workflow_id == proposal.workflow_run_id,
                OutboxRow.event_type == EventType.APPROVAL_REJECTED.value,
            )
        ).all()
        proposal_id = str(proposal.id)
        return any(str(row.payload.get("proposal_id")) == proposal_id for row in rows)

    def _move(
        self,
        row: ApprovalProcess,
        state: ApprovalProcessState,
        signal: ProcessSignal,
        moment: datetime,
    ) -> None:
        row.state = state.value
        row.last_signal = signal.kind.value
        row.arguments_hash = signal.arguments_hash
        row.updated_at = moment

    def _require_proposal(self, row: ApprovalProcess) -> OperationalProposal:
        proposal = self._proposal(row.tenant_id, row.proposal_id)
        if proposal is None:
            raise NotFoundError()
        return proposal

    def _reload(self, row: ApprovalProcess) -> ApprovalProcess:
        self._db.refresh(row)
        return row

    def _process(self, tenant_id: str, proposal_id: UUID) -> ApprovalProcess | None:
        return self._db.scalar(
            select(ApprovalProcess).where(
                ApprovalProcess.tenant_id == tenant_id,
                ApprovalProcess.proposal_id == proposal_id,
            )
        )

    def _proposal(self, tenant_id: str, proposal_id: UUID) -> OperationalProposal | None:
        return self._db.scalar(
            select(OperationalProposal).where(
                OperationalProposal.tenant_id == tenant_id,
                OperationalProposal.id == proposal_id,
            )
        )


def runner_for(db: Session, settings: Settings | None = None) -> ApprovalWait:
    """Use the local machine unless the managed state machine is actually on."""

    selected = get_settings() if settings is None else settings
    if selected.cloud_capability(selected.cloud_state_machine_enabled):
        from opsmesh_api.workflows.state_machine_cloud import StepFunctionsApprovalProcess

        return StepFunctionsApprovalProcess(db, selected)
    return LocalApprovalProcess(db)


def _commercial(arguments: dict[str, Any]) -> tuple[str, str, int, int] | None:
    sku = arguments.get("sku")
    supplier_id = arguments.get("supplier_id")
    quantity = arguments.get("quantity")
    unit_cost_cents = arguments.get("unit_cost_cents")
    if not isinstance(sku, str) or not sku:
        return None
    if not isinstance(supplier_id, str) or not supplier_id:
        return None
    if type(quantity) is not int or quantity < 1:
        return None
    if type(unit_cost_cents) is not int or unit_cost_cents < 0:
        return None
    return sku, supplier_id, quantity, unit_cost_cents
