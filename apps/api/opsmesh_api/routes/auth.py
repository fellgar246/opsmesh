"""Local development session. The hosted user pool replaces this route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.identity.issuer import LocalTokenIssuer
from opsmesh_api.identity.principal import ROLE_NAMES, Principal
from opsmesh_api.identity.provider import local_user_id
from opsmesh_api.services.models import Tenant
from opsmesh_api.settings import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LocalSessionRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)


class LocalSession(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user_id: str
    tenant_id: str
    roles: list[str]


@router.post("/session", response_model=LocalSession)
def open_local_session(
    body: LocalSessionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> LocalSession:
    """Issue a development token. This route is absent outside the local profile."""

    if not settings.uses_local_identity:
        raise HTTPException(status_code=404, detail="Not found")
    if body.role not in ROLE_NAMES:
        raise HTTPException(
            status_code=401,
            detail={"code": "unknown_role", "message": "The token role is not recognized."},
        )
    if db.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    principal = Principal(
        user_id=local_user_id(body.tenant_id, body.role),
        tenant_id=body.tenant_id,
        roles=frozenset({body.role}),
    )
    token = LocalTokenIssuer(
        settings.local_identity_secret,
        platform_role=settings.platform_role,
    ).issue(principal)
    return LocalSession(
        access_token=token,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        roles=sorted(principal.roles),
    )
