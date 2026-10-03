"""Purchase-order reads and local draft writes. Orders stay inside the simulator."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.services.errors import NotFoundError, ProcurementWriteError
from opsmesh_api.services.models import (
    IdempotencyRecord,
    PurchaseOrder,
    PurchaseOrderStatus,
    Supplier,
    SupplierTerms,
    Tenant,
)
from opsmesh_api.services.schemas import PurchaseOrderList, PurchaseOrderRead
from opsmesh_api.tools.schemas import CREATE_DRAFT_TOOL, SUBMIT_FOR_APPROVAL_TOOL
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import OperationalProposal, ProposalStatus

_COMMERCIAL_FIELDS = ("quantity", "sku", "supplier_id", "unit_cost_cents")
_DRAFTABLE_PROPOSALS = frozenset(
    {
        ProposalStatus.DRAFT.value,
        ProposalStatus.PENDING_APPROVAL.value,
        ProposalStatus.APPROVED.value,
    }
)
_CONFLICT = "The idempotency key was already used with different arguments."


class ProcurementService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_purchase_orders(self, tenant_id: str) -> PurchaseOrderList:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()
        rows = self._db.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .order_by(PurchaseOrder.purchase_order_id)
        ).all()
        return PurchaseOrderList(purchase_orders=[_order(row) for row in rows])

    def get_purchase_order(self, tenant_id: str, purchase_order_id: str) -> PurchaseOrderRead:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()
        row = self._require_order(tenant_id, purchase_order_id)
        return _order(row)

    def create_draft_purchase_order(
        self,
        tenant_id: str,
        *,
        proposal_id: UUID,
        sku: str,
        supplier_id: str,
        quantity: int,
        unit_cost_cents: int,
        idempotency_key: str,
        arguments_hash: str,
    ) -> PurchaseOrderRead:
        """Store one draft when the proposal is feasible and the arguments match."""

        replay = self._replay(tenant_id, idempotency_key, CREATE_DRAFT_TOOL, arguments_hash)
        if replay is not None:
            return replay
        proposal = self._proposal(tenant_id, proposal_id)
        if proposal is None:
            raise ProcurementWriteError("denied", "The proposal does not exist.")
        if proposal.status not in _DRAFTABLE_PROPOSALS:
            raise ProcurementWriteError("denied", "The proposal cannot accept a draft.")
        if not _feasible(proposal.arguments):
            raise ProcurementWriteError("denied", "The proposal is not feasible.")
        if not _same_commercial(
            proposal.arguments,
            sku=sku,
            supplier_id=supplier_id,
            quantity=quantity,
            unit_cost_cents=unit_cost_cents,
        ):
            raise ProcurementWriteError("denied", "The tool arguments do not match the proposal.")
        if self._db.get(Supplier, (tenant_id, supplier_id)) is None:
            raise ProcurementWriteError("denied", "The supplier does not exist.")
        purchase_order_id = f"po-{uuid4().hex}"
        order = PurchaseOrder(
            purchase_order_id=purchase_order_id,
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            sku=sku,
            quantity=quantity,
            status=PurchaseOrderStatus.DRAFT.value,
            expected_receipt_on=self._expected_receipt(tenant_id, supplier_id, sku),
            proposal_id=proposal_id,
            unit_cost_cents=unit_cost_cents,
            idempotency_key=idempotency_key,
            arguments_hash=arguments_hash,
        )
        record = _record(
            tenant_id,
            idempotency_key,
            CREATE_DRAFT_TOOL,
            arguments_hash,
            purchase_order_id,
        )
        self._db.add(order)
        self._db.flush()
        self._db.add(record)
        stored = self._commit_new(tenant_id, idempotency_key, CREATE_DRAFT_TOOL, arguments_hash)
        if stored is not None:
            return stored
        return _order(order)

    def submit_purchase_order_for_approval(
        self,
        tenant_id: str,
        *,
        purchase_order_id: str,
        idempotency_key: str,
        arguments_hash: str,
    ) -> PurchaseOrderRead:
        """Move a draft to waiting for approval. This does not approve the proposal."""

        replay = self._replay(
            tenant_id,
            idempotency_key,
            SUBMIT_FOR_APPROVAL_TOOL,
            arguments_hash,
        )
        if replay is not None:
            return replay
        order = self._find_order(tenant_id, purchase_order_id)
        if order is None or order.status != PurchaseOrderStatus.DRAFT.value:
            raise ProcurementWriteError("denied", "The purchase order is not a draft.")
        proposal_id = order.proposal_id
        unit_cost_cents = order.unit_cost_cents
        if proposal_id is None or unit_cost_cents is None:
            raise ProcurementWriteError("denied", "The proposal does not exist.")
        proposal = self._proposal(tenant_id, proposal_id)
        if proposal is None:
            raise ProcurementWriteError("denied", "The proposal does not exist.")
        requires_manager, requires_finance = approval_for_total(order.quantity * unit_cost_cents)
        if (requires_manager or requires_finance) and proposal.status == ProposalStatus.DRAFT.value:
            proposal.status = ProposalStatus.PENDING_APPROVAL.value
        order.status = PurchaseOrderStatus.WAITING_APPROVAL.value
        self._db.add(
            _record(
                tenant_id,
                idempotency_key,
                SUBMIT_FOR_APPROVAL_TOOL,
                arguments_hash,
                order.purchase_order_id,
            )
        )
        stored = self._commit_new(
            tenant_id,
            idempotency_key,
            SUBMIT_FOR_APPROVAL_TOOL,
            arguments_hash,
        )
        if stored is not None:
            return stored
        return _order(order)

    def _replay(
        self,
        tenant_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments_hash: str,
    ) -> PurchaseOrderRead | None:
        record = self._db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == tenant_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if record is not None:
            _require_same_write(record, tool_name, arguments_hash)
            return _order(self._require_order(tenant_id, record.result_reference))
        if tool_name != CREATE_DRAFT_TOOL:
            return None
        order = self._db.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.idempotency_key == idempotency_key,
            )
        )
        if order is None:
            return None
        if order.arguments_hash != arguments_hash:
            raise ProcurementWriteError("idempotency_conflict", _CONFLICT)
        self._db.add(
            _record(tenant_id, idempotency_key, tool_name, arguments_hash, order.purchase_order_id)
        )
        stored = self._commit_new(tenant_id, idempotency_key, tool_name, arguments_hash)
        if stored is not None:
            return stored
        return _order(order)

    def _commit_new(
        self,
        tenant_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments_hash: str,
    ) -> PurchaseOrderRead | None:
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            record = self._db.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == tenant_id,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
            if record is None:
                raise ProcurementWriteError("idempotency_conflict", _CONFLICT) from None
            _require_same_write(record, tool_name, arguments_hash)
            return _order(self._require_order(tenant_id, record.result_reference))
        return None

    def _proposal(self, tenant_id: str, proposal_id: UUID) -> OperationalProposal | None:
        return self._db.scalar(
            select(OperationalProposal).where(
                OperationalProposal.id == proposal_id,
                OperationalProposal.tenant_id == tenant_id,
            )
        )

    def _find_order(self, tenant_id: str, purchase_order_id: str) -> PurchaseOrder | None:
        return self._db.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.purchase_order_id == purchase_order_id,
            )
        )

    def _require_order(self, tenant_id: str, purchase_order_id: str) -> PurchaseOrder:
        row = self._find_order(tenant_id, purchase_order_id)
        if row is None:
            raise NotFoundError()
        return row

    def _expected_receipt(self, tenant_id: str, supplier_id: str, sku: str) -> date:
        terms = self._db.get(SupplierTerms, (tenant_id, supplier_id, sku))
        lead_days = terms.lead_time_days if terms is not None else 0
        return datetime.now(UTC).date() + timedelta(days=lead_days)


def _record(
    tenant_id: str,
    idempotency_key: str,
    tool_name: str,
    arguments_hash: str,
    purchase_order_id: str,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        tool_name=tool_name,
        arguments_hash=arguments_hash,
        result_reference=purchase_order_id,
    )


def _require_same_write(record: IdempotencyRecord, tool_name: str, arguments_hash: str) -> None:
    if record.arguments_hash != arguments_hash or record.tool_name != tool_name:
        raise ProcurementWriteError("idempotency_conflict", _CONFLICT)


def _feasible(arguments: dict[str, Any]) -> bool:
    if arguments.get("blocked") is True:
        return False
    if arguments.get("reason") is not None:
        return False
    quantity = arguments.get("quantity")
    return type(quantity) is int and quantity > 0


def _same_commercial(
    arguments: dict[str, Any],
    *,
    sku: str,
    supplier_id: str,
    quantity: int,
    unit_cost_cents: int,
) -> bool:
    try:
        stored = {field: arguments[field] for field in _COMMERCIAL_FIELDS}
    except KeyError:
        return False
    presented = {
        "quantity": quantity,
        "sku": sku,
        "supplier_id": supplier_id,
        "unit_cost_cents": unit_cost_cents,
    }
    return canonical_json_hash(stored) == canonical_json_hash(presented)


def _order(row: PurchaseOrder) -> PurchaseOrderRead:
    return PurchaseOrderRead(
        purchase_order_id=row.purchase_order_id,
        tenant_id=row.tenant_id,
        supplier_id=row.supplier_id,
        sku=row.sku,
        quantity=row.quantity,
        status=PurchaseOrderStatus(row.status),
        expected_receipt_on=row.expected_receipt_on,
        proposal_id=None if row.proposal_id is None else str(row.proposal_id),
        unit_cost_cents=row.unit_cost_cents,
        idempotency_key=row.idempotency_key,
    )
