"""Amazon Cognito token verification.

The JSON Web Key Set is fetched only when a token is verified with the hosted
provider. Tests pass a key source and do not call the user pool.
"""

import json
import urllib.request
from typing import Any, Protocol

import jwt
from jwt import PyJWK

from opsmesh_api.identity.claims import principal_from_claims
from opsmesh_api.identity.principal import IdentityRejected, Principal, normalize_platform_role

_ALGORITHM = "RS256"


class JwksSource(Protocol):
    """Resolve a key id to a verification key."""

    def get_key(self, kid: str) -> Any:
        """Return the public key for this key id."""


class UrlJwksSource:
    """Fetch a user-pool key set over HTTPS. The result is cached on this instance."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._keys: dict[str, Any] | None = None

    def get_key(self, kid: str) -> Any:
        if self._keys is None:
            self._keys = _fetch_keys(self._url)
        key = self._keys.get(kid)
        if key is None:
            raise IdentityRejected("invalid_token", "The token could not be verified.")
        return key


class CognitoTokenVerifier:
    """Verify a Cognito access or ID token and read tenant and role claims."""

    def __init__(
        self,
        *,
        region: str,
        user_pool_id: str,
        client_id: str,
        platform_role: str = "",
        keys: JwksSource | None = None,
    ) -> None:
        pool = user_pool_id.strip()
        self._region = region.strip() or "us-east-1"
        self._pool_id = pool
        self._client_id = client_id.strip()
        self._platform_role = normalize_platform_role(platform_role)
        self._issuer = f"https://cognito-idp.{self._region}.amazonaws.com/{pool}"
        self._keys = keys
        self._remote: UrlJwksSource | None = None

    def verify(self, token: str) -> Principal:
        if not self._pool_id or not self._client_id:
            raise IdentityRejected("invalid_token", "The token could not be verified.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityRejected("invalid_token", "The token could not be verified.") from exc
        kid = header.get("kid") if isinstance(header, dict) else None
        if not isinstance(kid, str) or not kid:
            raise IdentityRejected("invalid_token", "The token could not be verified.")
        try:
            claims = jwt.decode(
                token,
                self._source().get_key(kid),
                algorithms=[_ALGORITHM],
                issuer=self._issuer,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise IdentityRejected("invalid_token", "The token could not be verified.") from exc
        if not isinstance(claims, dict):
            raise IdentityRejected("invalid_token", "The token could not be verified.")
        _require_client(claims, self._client_id)
        return principal_from_claims(claims, platform_role=self._platform_role)

    def _source(self) -> JwksSource:
        if self._keys is not None:
            return self._keys
        if self._remote is None:
            self._remote = UrlJwksSource(f"{self._issuer}/.well-known/jwks.json")
        return self._remote


def _require_client(claims: dict[str, Any], client_id: str) -> None:
    token_use = claims.get("token_use")
    if token_use == "id" and claims.get("aud") == client_id:
        return
    if token_use == "access" and claims.get("client_id") == client_id:
        return
    raise IdentityRejected("invalid_token", "The token could not be verified.")


def _fetch_keys(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except Exception as exc:
        raise IdentityRejected("invalid_token", "The token could not be verified.") from exc
    keys = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(keys, list):
        raise IdentityRejected("invalid_token", "The token could not be verified.")
    found: dict[str, Any] = {}
    for item in keys:
        if not isinstance(item, dict):
            continue
        kid = item.get("kid")
        if isinstance(kid, str) and kid:
            found[kid] = PyJWK(item).key
    return found
