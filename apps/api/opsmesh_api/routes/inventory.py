"""Inventory read routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.services.inventory import InventoryService
from opsmesh_api.services.schemas import InventorySnapshot, ReorderParameters, StockoutList

router = APIRouter(prefix="/tenants/{tenant_id}/inventory", tags=["inventory"])


def _service(db: Annotated[Session, Depends(get_db)]) -> InventoryService:
    return InventoryService(db)


@router.get("/skus/{sku}/regions/{region}", response_model=InventorySnapshot)
def snapshot(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[InventoryService, Depends(_service)],
) -> InventorySnapshot:
    """On-hand, reserved, available, reorder point, and observation time."""

    return call(lambda: service.get_snapshot(tenant_id, sku, region))


@router.get("/skus/{sku}/regions/{region}/stockouts", response_model=StockoutList)
def stockouts(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[InventoryService, Depends(_service)],
) -> StockoutList:
    return call(lambda: service.get_stockout_history(tenant_id, sku, region))


@router.get(
    "/skus/{sku}/regions/{region}/reorder-parameters",
    response_model=ReorderParameters,
)
def reorder_parameters(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[InventoryService, Depends(_service)],
) -> ReorderParameters:
    return call(lambda: service.get_reorder_parameters(tenant_id, sku, region))
