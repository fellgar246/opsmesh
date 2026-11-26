"""Start deterministic operational workflows for one tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.agents.routing import select_agent_client, select_planner
from opsmesh_api.db import get_db
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.schemas import (
    BudgetPlanRead,
    BudgetPlanRequest,
    ReorderRead,
    ReorderRequest,
    SupplierDelayRead,
    SupplierDelayRequest,
)
from opsmesh_api.routes.errors import call
from opsmesh_api.settings import Settings, get_settings

router = APIRouter(prefix="/tenants/{tenant_id}/operations", tags=["operations"])


def _engine(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OperationalEngine:
    return OperationalEngine(
        db,
        agent_client=select_agent_client(db, settings),
        planner=select_planner(settings),
    )


@router.post("/reorder", response_model=ReorderRead)
def reorder(
    tenant_id: str,
    body: ReorderRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> ReorderRead:
    return call(lambda: engine.reorder(tenant_id, body))


@router.post("/supplier-delays", response_model=SupplierDelayRead)
def supplier_delay(
    tenant_id: str,
    body: SupplierDelayRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> SupplierDelayRead:
    return call(lambda: engine.supplier_delay(tenant_id, body))


@router.post("/budget-plans", response_model=BudgetPlanRead)
def budget_plan(
    tenant_id: str,
    body: BudgetPlanRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> BudgetPlanRead:
    return call(lambda: engine.budget_plan(tenant_id, body))
