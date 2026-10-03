"""Map verified token claims onto a principal."""

from collections.abc import Mapping
from typing import Any

from opsmesh_api.identity.principal import ROLE_NAMES, IdentityRejected, Principal


def principal_from_claims(claims: Mapping[str, Any], *, platform_role: str) -> Principal:
    """Read the subject, tenant, and roles. Missing or unknown values are rejected."""

    tenant = _text(claims.get("tenant_id")) or _text(claims.get("custom:tenant_id"))
    if tenant is None:
        raise IdentityRejected("missing_tenant", "The token has no tenant.")
    user_id = _text(claims.get("sub")) or _text(claims.get("username"))
    if user_id is None:
        raise IdentityRejected("invalid_token", "The token has no subject.")
    roles, platform = _roles(claims.get("cognito:groups"), platform_role)
    if not roles and not platform:
        roles, platform = _roles(claims.get("roles"), platform_role)
    if not roles and not platform:
        raise IdentityRejected("unknown_role", "The token role is not recognized.")
    return Principal(user_id=user_id, tenant_id=tenant, roles=roles, platform=platform)


def _roles(raw: object, platform_role: str) -> tuple[frozenset[str], bool]:
    if raw is None:
        return frozenset(), False
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise IdentityRejected("unknown_role", "The token role is not recognized.")
    if not values:
        raise IdentityRejected("unknown_role", "The token role is not recognized.")
    roles: set[str] = set()
    platform = False
    for item in values:
        name = _text(item)
        if name is None:
            raise IdentityRejected("unknown_role", "The token role is not recognized.")
        if name in ROLE_NAMES:
            roles.add(name)
            continue
        if platform_role and name == platform_role:
            platform = True
            continue
        raise IdentityRejected("unknown_role", "The token role is not recognized.")
    return frozenset(roles), platform


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped
