"""Require a verified principal on business routes."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from opsmesh_api.identity.access import allow
from opsmesh_api.identity.principal import IdentityRejected, Principal
from opsmesh_api.identity.provider import TokenVerifier, verifier_for
from opsmesh_api.settings import Settings, get_settings

_START_ROLE = "operations_analyst"


def get_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> TokenVerifier:
    return verifier_for(settings)


def get_principal(
    verifier: Annotated[TokenVerifier, Depends(get_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Read tenant and roles from a verified bearer token."""

    if authorization is None or not authorization.lower().startswith("bearer "):
        raise _unauthenticated()
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise _unauthenticated()
    try:
        return verifier.verify(token)
    except IdentityRejected as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def bind_tenant(
    tenant_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Keep the path tenant aligned with the token. A mismatch is not found."""

    if tenant_id == principal.tenant_id:
        return principal
    if principal.platform and request.method == "GET":
        return principal
    raise HTTPException(status_code=404, detail="Not found")


def require_workflow_start(
    tenant_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Start stays inside the token tenant and requires an operations analyst."""

    if tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    if not allow(
        user=_START_ROLE in principal.roles,
        agent=True,
        tool=True,
        policy=True,
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "role_denied",
                "message": "Requires operations_analyst role.",
            },
        )
    return principal


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "unauthenticated", "message": "A bearer token is required."},
    )
