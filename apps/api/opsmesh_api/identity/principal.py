"""Callers the API will trust after a token verifies."""

from dataclasses import dataclass

ROLE_NAMES = frozenset(
    {
        "viewer",
        "operations_analyst",
        "procurement_manager",
        "finance_approver",
        "admin",
    }
)


class IdentityRejected(Exception):
    """The token cannot be used. The message does not describe another tenant."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    """End user. A tenant admin is not a platform operator."""

    user_id: str
    tenant_id: str
    roles: frozenset[str]
    platform: bool = False


@dataclass(frozen=True)
class AgentPrincipal:
    """Specialist identity bound to one workflow tenant. This is not an end-user token."""

    agent_type: str
    runtime_identity: str
    tenant_id: str


def normalize_platform_role(value: str) -> str:
    """A platform role must be a separate name. Tenant roles stay tenant-scoped."""

    name = value.strip()
    if not name or name in ROLE_NAMES:
        return ""
    return name
