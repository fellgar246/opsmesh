"""Approval decisions for one tenant. The same rules apply outside the console."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opsmesh_api.approvals.engine import Actor, ApprovalService
from opsmesh_api.approvals.errors import ApprovalError
from opsmesh_api.approvals.schemas import ApprovalDecision, ApprovalDetail, ApprovalQueue
from opsmesh_api.db import get_db
from opsmesh_api.identity.principal import Principal
from opsmesh_api.routes.errors import call
from opsmesh_api.routes.security import bind_tenant

router = APIRouter(
    prefix="/tenants/{tenant_id}/approvals",
    tags=["approvals"],
    dependencies=[Depends(bind_tenant)],
)


def _service(db: Annotated[Session, Depends(get_db)]) -> ApprovalService:
    return ApprovalService(db)


def _http(exc: ApprovalError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


@router.get("", response_model=ApprovalQueue)
def list_approvals(
    tenant_id: str,
    service: Annotated[ApprovalService, Depends(_service)],
) -> ApprovalQueue:
    return call(lambda: service.list_approvals(tenant_id))


@router.get("/{approval_id}", response_model=ApprovalDetail)
def get_approval(
    tenant_id: str,
    approval_id: UUID,
    service: Annotated[ApprovalService, Depends(_service)],
) -> ApprovalDetail:
    return call(lambda: service.get_approval(tenant_id, approval_id))


@router.post("/{approval_id}/decision", response_model=ApprovalDetail)
def decide_approval(
    tenant_id: str,
    approval_id: UUID,
    body: ApprovalDecision,
    service: Annotated[ApprovalService, Depends(_service)],
    principal: Annotated[Principal, Depends(bind_tenant)],
) -> ApprovalDetail:
    actor = Actor(actor=principal.user_id, roles=principal.roles)
    return call(
        lambda: service.decide(
            tenant_id,
            approval_id,
            actor,
            decision=body.decision,
            comment=body.comment,
        )
    )
