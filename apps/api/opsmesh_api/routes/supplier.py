"""Supplier read routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.services.schemas import (
    PreferredSupplierRead,
    PurchaseOrderList,
    ReliabilityRead,
    SupplierDelayRead,
    SupplierTermsRead,
)
from opsmesh_api.services.supplier import SupplierService

router = APIRouter(prefix="/tenants/{tenant_id}/suppliers", tags=["supplier"])


def _service(db: Annotated[Session, Depends(get_db)]) -> SupplierService:
    return SupplierService(db)


@router.get("/preferred", response_model=PreferredSupplierRead)
def preferred_supplier(
    tenant_id: str,
    service: Annotated[SupplierService, Depends(_service)],
    sku: Annotated[str, Query(min_length=1)],
) -> PreferredSupplierRead:
    return call(lambda: service.get_preferred(tenant_id, sku))


@router.get("/{supplier_id}/terms", response_model=SupplierTermsRead)
def supplier_terms(
    tenant_id: str,
    supplier_id: str,
    service: Annotated[SupplierService, Depends(_service)],
    sku: Annotated[str, Query(min_length=1)],
) -> SupplierTermsRead:
    return call(lambda: service.get_terms(tenant_id, supplier_id, sku))


@router.get("/{supplier_id}/reliability", response_model=ReliabilityRead)
def supplier_reliability(
    tenant_id: str,
    supplier_id: str,
    service: Annotated[SupplierService, Depends(_service)],
) -> ReliabilityRead:
    return call(lambda: service.get_reliability(tenant_id, supplier_id))


@router.get("/{supplier_id}/purchase-orders", response_model=PurchaseOrderList)
def open_purchase_orders(
    tenant_id: str,
    supplier_id: str,
    service: Annotated[SupplierService, Depends(_service)],
) -> PurchaseOrderList:
    return call(lambda: service.list_open_purchase_orders(tenant_id, supplier_id))


@router.get("/{supplier_id}/delay", response_model=SupplierDelayRead)
def supplier_delay(
    tenant_id: str,
    supplier_id: str,
    service: Annotated[SupplierService, Depends(_service)],
) -> SupplierDelayRead:
    return call(lambda: service.get_delay(tenant_id, supplier_id))
