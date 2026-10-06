"""Start deterministic operational workflows for one tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.agents.ceilings import MAX_WORKFLOWS_PER_DAY
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
from opsmesh_api.routes.security import require_workflow_start
from opsmesh_api.settings import Settings, get_settings
from opsmesh_api.workflows.errors import DailyLimitError

router = APIRouter(
    prefix="/tenants/{tenant_id}/operations",
    tags=["operations"],
    dependencies=[Depends(require_workflow_start)],
)


def _engine(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OperationalEngine:
    return OperationalEngine(
        db,
        agent_client=select_agent_client(db, settings),
        planner=select_planner(settings),
    )


def _within_daily_quota(engine: OperationalEngine, tenant_id: str) -> None:
    if engine.started_today(tenant_id) >= MAX_WORKFLOWS_PER_DAY:
        raise DailyLimitError()


@router.post("/reorder", response_model=ReorderRead)
def reorder(
    tenant_id: str,
    body: ReorderRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> ReorderRead:
    def start() -> ReorderRead:
        _within_daily_quota(engine, tenant_id)
        return engine.reorder(tenant_id, body)

    return call(start)


@router.post("/supplier-delays", response_model=SupplierDelayRead)
def supplier_delay(
    tenant_id: str,
    body: SupplierDelayRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> SupplierDelayRead:
    def start() -> SupplierDelayRead:
        _within_daily_quota(engine, tenant_id)
        return engine.supplier_delay(tenant_id, body)

    return call(start)


@router.post("/budget-plans", response_model=BudgetPlanRead)
def budget_plan(
    tenant_id: str,
    body: BudgetPlanRequest,
    engine: Annotated[OperationalEngine, Depends(_engine)],
) -> BudgetPlanRead:
    def start() -> BudgetPlanRead:
        _within_daily_quota(engine, tenant_id)
        return engine.budget_plan(tenant_id, body)

    return call(start)
