"""Per-workflow cost for the active tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.agents.ceilings import MAX_WORKFLOWS_PER_DAY
from opsmesh_api.cost.schema import CostReport
from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.routes.security import bind_tenant
from opsmesh_api.workflows.store import WorkflowStore

router = APIRouter(
    prefix="/tenants/{tenant_id}/cost",
    tags=["cost"],
    dependencies=[Depends(bind_tenant)],
)


@router.get("", response_model=CostReport)
def cost_report(
    tenant_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> CostReport:
    def load() -> CostReport:
        store = WorkflowStore(db)
        return CostReport(
            workflows_started_today=store.count_started_today(tenant_id),
            daily_ceiling=MAX_WORKFLOWS_PER_DAY,
            workflows=store.usage_ledgers(tenant_id),
        )

    return call(load)
