"""Select the token verifier for the active execution profile."""

from typing import Protocol

from opsmesh_api.identity.cognito import CognitoTokenVerifier
from opsmesh_api.identity.issuer import LocalTokenIssuer
from opsmesh_api.identity.principal import Principal
from opsmesh_api.settings import Settings


class TokenVerifier(Protocol):
    """Verify one bearer token."""

    def verify(self, token: str) -> Principal:
        """Return the principal or reject the token."""


def verifier_for(settings: Settings) -> TokenVerifier:
    """Use the local issuer on the local profile. Otherwise verify Cognito tokens."""

    if settings.uses_local_identity:
        return LocalTokenIssuer(
            settings.local_identity_secret,
            platform_role=settings.platform_role,
        )
    return CognitoTokenVerifier(
        region=settings.cognito_region,
        user_pool_id=settings.cognito_user_pool_id,
        client_id=settings.cognito_client_id,
        platform_role=settings.platform_role,
    )


def local_user_id(tenant_id: str, role: str) -> str:
    """Stable demo subject for a tenant and role."""

    local = {
        "viewer": "viewer",
        "operations_analyst": "analyst",
        "procurement_manager": "manager",
        "finance_approver": "finance",
        "admin": "admin",
    }.get(role, role)
    return f"{local}@{tenant_id}.example"
