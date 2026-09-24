"""Purchase-order reads. Orders stay inside the simulator."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import PurchaseOrder, PurchaseOrderStatus, Tenant
from opsmesh_api.services.schemas import PurchaseOrderList, PurchaseOrderRead


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
        row = self._db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.purchase_order_id == purchase_order_id,
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError()
        return _order(row)


def _order(row: PurchaseOrder) -> PurchaseOrderRead:
    return PurchaseOrderRead(
        purchase_order_id=row.purchase_order_id,
        tenant_id=row.tenant_id,
        supplier_id=row.supplier_id,
        sku=row.sku,
        quantity=row.quantity,
        status=PurchaseOrderStatus(row.status),
        expected_receipt_on=row.expected_receipt_on,
    )
