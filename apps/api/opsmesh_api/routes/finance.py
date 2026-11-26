"""Finance read routes. Purchase impact is computed and not stored."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.services.finance import FinanceService
from opsmesh_api.services.schemas import BudgetRead, CommittedSpendRead, PurchaseImpactRead

router = APIRouter(prefix="/tenants/{tenant_id}/finance", tags=["finance"])


def _service(db: Annotated[Session, Depends(get_db)]) -> FinanceService:
    return FinanceService(db)


@router.get("/budget", response_model=BudgetRead)
def budget(
    tenant_id: str,
    service: Annotated[FinanceService, Depends(_service)],
) -> BudgetRead:
    return call(lambda: service.get_budget(tenant_id))


@router.get("/committed-spend", response_model=CommittedSpendRead)
def committed_spend(
    tenant_id: str,
    service: Annotated[FinanceService, Depends(_service)],
) -> CommittedSpendRead:
    return call(lambda: service.get_committed_spend(tenant_id))


@router.get("/purchase-impact", response_model=PurchaseImpactRead)
def purchase_impact(
    tenant_id: str,
    service: Annotated[FinanceService, Depends(_service)],
    quantity: Annotated[int, Query(ge=0)],
    unit_cost_cents: Annotated[int, Query(ge=0)],
) -> PurchaseImpactRead:
    return call(lambda: service.estimate(tenant_id, quantity, unit_cost_cents))
