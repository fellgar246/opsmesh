"""Latest evaluation report for the active tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.evaluation.schema import EvaluationLatest
from opsmesh_api.evaluation.store import latest_runs
from opsmesh_api.routes.errors import call
from opsmesh_api.routes.security import bind_tenant

router = APIRouter(
    prefix="/tenants/{tenant_id}/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(bind_tenant)],
)


@router.get("/latest", response_model=EvaluationLatest)
def latest_evaluation(
    tenant_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationLatest:
    def load() -> EvaluationLatest:
        current, previous = latest_runs(db, tenant_id)
        return EvaluationLatest(run=current, previous=previous)

    return call(load)
