"""Development token issuer for the local execution profile.

Tokens use the same claim names as the hosted user pool. Verification stays
in process and does not contact a hosted identity service.
"""

from time import time
from typing import Any

import jwt

from opsmesh_api.identity.claims import principal_from_claims
from opsmesh_api.identity.principal import IdentityRejected, Principal, normalize_platform_role

LOCAL_ISSUER = "https://opsmesh.local/identity"
LOCAL_AUDIENCE = "opsmesh-local"
_ALGORITHM = "HS256"


class LocalTokenIssuer:
    """Sign and verify local development tokens."""

    def __init__(self, secret: str, *, platform_role: str = "") -> None:
        self._secret = secret
        self._platform_role = normalize_platform_role(platform_role)

    def issue(self, principal: Principal, *, ttl_seconds: int = 12 * 60 * 60) -> str:
        """Sign one principal. Groups use the hosted claim name."""

        groups = sorted(principal.roles)
        if principal.platform and self._platform_role:
            groups.append(self._platform_role)
        return self.issue_claims(
            {
                "sub": principal.user_id,
                "tenant_id": principal.tenant_id,
                "cognito:groups": groups,
            },
            ttl_seconds=ttl_seconds,
        )

    def issue_claims(self, claims: dict[str, Any], *, ttl_seconds: int = 12 * 60 * 60) -> str:
        """Sign claims as-is, filling issuer, audience, and expiry when absent."""

        now = int(time())
        payload = {
            "iss": LOCAL_ISSUER,
            "aud": LOCAL_AUDIENCE,
            "iat": now,
            "exp": now + ttl_seconds,
            **claims,
        }
        encoded = jwt.encode(payload, self._secret, algorithm=_ALGORITHM)
        if isinstance(encoded, bytes):
            return encoded.decode("ascii")
        return encoded

    def verify(self, token: str) -> Principal:
        """Verify the local signature and map claims. Hosted keys are not fetched."""

        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                audience=LOCAL_AUDIENCE,
                issuer=LOCAL_ISSUER,
            )
        except jwt.PyJWTError as exc:
            raise IdentityRejected("invalid_token", "The token could not be verified.") from exc
        if not isinstance(claims, dict):
            raise IdentityRejected("invalid_token", "The token could not be verified.")
        return principal_from_claims(claims, platform_role=self._platform_role)
