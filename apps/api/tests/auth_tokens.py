"""Local bearer tokens for API tests. These tokens are not issued by a hosted pool."""

from opsmesh_api.identity.issuer import LocalTokenIssuer
from opsmesh_api.identity.principal import Principal
from opsmesh_api.settings import Settings


def token_for(
    actor: str,
    role: str,
    tenant_id: str = "northwind",
    *,
    platform: bool = False,
    platform_role: str = "",
    ttl_seconds: int = 12 * 60 * 60,
) -> str:
    secret = Settings(_env_file=None).local_identity_secret
    issuer = LocalTokenIssuer(secret, platform_role=platform_role)
    principal = Principal(
        user_id=actor,
        tenant_id=tenant_id,
        roles=frozenset({role}) if role else frozenset(),
        platform=platform,
    )
    return issuer.issue(principal, ttl_seconds=ttl_seconds)


def bearer(
    actor: str,
    role: str,
    tenant_id: str = "northwind",
    *,
    platform: bool = False,
    platform_role: str = "",
) -> dict[str, str]:
    token = token_for(
        actor,
        role,
        tenant_id,
        platform=platform,
        platform_role=platform_role,
    )
    return {"Authorization": f"Bearer {token}"}
