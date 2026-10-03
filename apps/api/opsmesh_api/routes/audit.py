"""Tenant-scoped audit projection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.events.consumers import list_audit_events
from opsmesh_api.events.schemas import AuditEventList
from opsmesh_api.routes.errors import call
from opsmesh_api.routes.security import bind_tenant

router = APIRouter(
    prefix="/tenants/{tenant_id}/audit-events",
    tags=["audit"],
    dependencies=[Depends(bind_tenant)],
)


@router.get("", response_model=AuditEventList)
def list_events(
    tenant_id: str,
    db: Annotated[Session, Depends(get_db)],
    workflow_id: UUID | None = None,
) -> AuditEventList:
    return call(lambda: list_audit_events(db, tenant_id, workflow_id))
