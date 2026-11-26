"""Procurement read routes. Nothing here sends an order to a supplier."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.services.procurement import ProcurementService
from opsmesh_api.services.schemas import PurchaseOrderList, PurchaseOrderRead

router = APIRouter(prefix="/tenants/{tenant_id}/procurement", tags=["procurement"])


def _service(db: Annotated[Session, Depends(get_db)]) -> ProcurementService:
    return ProcurementService(db)


@router.get("/purchase-orders", response_model=PurchaseOrderList)
def list_purchase_orders(
    tenant_id: str,
    service: Annotated[ProcurementService, Depends(_service)],
) -> PurchaseOrderList:
    return call(lambda: service.list_purchase_orders(tenant_id))


@router.get("/purchase-orders/{purchase_order_id}", response_model=PurchaseOrderRead)
def get_purchase_order(
    tenant_id: str,
    purchase_order_id: str,
    service: Annotated[ProcurementService, Depends(_service)],
) -> PurchaseOrderRead:
    return call(lambda: service.get_purchase_order(tenant_id, purchase_order_id))
