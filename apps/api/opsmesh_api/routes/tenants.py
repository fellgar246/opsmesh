"""List tenants the local console can select."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.services.models import Tenant
from opsmesh_api.services.schemas import TenantList, TenantRead

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("", response_model=TenantList)
def list_tenants(db: Annotated[Session, Depends(get_db)]) -> TenantList:
    rows = db.scalars(select(Tenant).order_by(Tenant.name)).all()
    return TenantList(
        tenants=[TenantRead(tenant_id=row.tenant_id, name=row.name) for row in rows]
    )
