"""Supplier terms, reliability, open orders, and active delays."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import (
    PurchaseOrder,
    PurchaseOrderStatus,
    Supplier,
    SupplierDelay,
    SupplierStatus,
    SupplierTerms,
    Tenant,
)
from opsmesh_api.services.schemas import (
    PreferredSupplierRead,
    PurchaseOrderList,
    PurchaseOrderRead,
    ReliabilityRead,
    SupplierDelayRead,
    SupplierTermsRead,
)


def _require_tenant(db: Session, tenant_id: str) -> None:
    if db.get(Tenant, tenant_id) is None:
        raise NotFoundError()


class SupplierService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_preferred(self, tenant_id: str, sku: str) -> PreferredSupplierRead:
        _require_tenant(self._db, tenant_id)
        rows = self._db.execute(
            select(SupplierTerms, Supplier)
            .join(
                Supplier,
                (Supplier.tenant_id == SupplierTerms.tenant_id)
                & (Supplier.supplier_id == SupplierTerms.supplier_id),
            )
            .where(
                SupplierTerms.tenant_id == tenant_id,
                SupplierTerms.sku == sku,
                SupplierTerms.preferred.is_(True),
            )
            .order_by(SupplierTerms.supplier_id)
        ).all()
        if len(rows) != 1:
            raise NotFoundError()
        terms, supplier = rows[0]
        return PreferredSupplierRead(
            tenant_id=terms.tenant_id,
            supplier_id=terms.supplier_id,
            sku=terms.sku,
            status=SupplierStatus(supplier.status),
        )

    def get_terms(self, tenant_id: str, supplier_id: str, sku: str) -> SupplierTermsRead:
        terms, supplier = self._terms_and_supplier(tenant_id, supplier_id, sku)
        return SupplierTermsRead(
            tenant_id=terms.tenant_id,
            supplier_id=terms.supplier_id,
            sku=terms.sku,
            lead_time_days=terms.lead_time_days,
            minimum_order_quantity=terms.minimum_order_quantity,
            case_pack=terms.case_pack,
            unit_cost_cents=terms.unit_cost_cents,
            status=SupplierStatus(supplier.status),
            reliability_score=supplier.reliability_score,
        )

    def get_reliability(self, tenant_id: str, supplier_id: str) -> ReliabilityRead:
        supplier = self._supplier(tenant_id, supplier_id)
        return ReliabilityRead(
            tenant_id=supplier.tenant_id,
            supplier_id=supplier.supplier_id,
            status=SupplierStatus(supplier.status),
            reliability_score=supplier.reliability_score,
        )

    def list_open_purchase_orders(self, tenant_id: str, supplier_id: str) -> PurchaseOrderList:
        self._supplier(tenant_id, supplier_id)
        rows = self._db.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status == PurchaseOrderStatus.OPEN.value,
            )
            .order_by(PurchaseOrder.purchase_order_id)
        ).all()
        return PurchaseOrderList(purchase_orders=[_order(row) for row in rows])

    def get_delay(self, tenant_id: str, supplier_id: str) -> SupplierDelayRead:
        self._supplier(tenant_id, supplier_id)
        row = self._db.scalars(
            select(SupplierDelay)
            .where(
                SupplierDelay.tenant_id == tenant_id,
                SupplierDelay.supplier_id == supplier_id,
                SupplierDelay.active.is_(True),
            )
            .order_by(SupplierDelay.delay_id)
        ).first()
        if row is None:
            raise NotFoundError()
        return SupplierDelayRead(
            delay_id=row.delay_id,
            tenant_id=row.tenant_id,
            supplier_id=row.supplier_id,
            active=row.active,
            delay_days=row.delay_days,
            reason=row.reason,
            started_at=row.started_at,
        )

    def _supplier(self, tenant_id: str, supplier_id: str) -> Supplier:
        _require_tenant(self._db, tenant_id)
        supplier = self._db.get(Supplier, (tenant_id, supplier_id))
        if supplier is None:
            raise NotFoundError()
        return supplier

    def _terms_and_supplier(
        self, tenant_id: str, supplier_id: str, sku: str
    ) -> tuple[SupplierTerms, Supplier]:
        supplier = self._supplier(tenant_id, supplier_id)
        terms = self._db.get(SupplierTerms, (tenant_id, supplier_id, sku))
        if terms is None:
            raise NotFoundError()
        return terms, supplier


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
