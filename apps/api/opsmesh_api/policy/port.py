"""Policy decision shared by the local engine and the optional managed adapter.

Either deny is a deny. The decision does not carry a quantity or an approval waiver.
"""

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

REASON_MANAGED_DENIED = "managed_policy_denied"
REASON_MANAGED_UNAVAILABLE = "managed_policy_unavailable"


class PolicyEffect(StrEnum):
    """Allow or deny. There is no third outcome."""

    ALLOW = "allow"
    DENY = "deny"


class PolicyLayer(StrEnum):
    """Which voter produced a vote."""

    LOCAL = "local"
    MANAGED = "managed"


class OperationKind(StrEnum):
    """An agent run or a tool call. Both can take the managed vote."""

    AGENT = "agent"
    TOOL = "tool"


class LayerVote(BaseModel):
    """One layer's vote. A vote cannot change a proposal quantity."""

    model_config = ConfigDict(extra="forbid")

    layer: PolicyLayer
    effect: PolicyEffect
    reason_codes: tuple[str, ...] = ()


class PolicyDecision(BaseModel):
    """Effective allow or deny, the reason codes, and every layer that voted."""

    model_config = ConfigDict(extra="forbid")

    effect: PolicyEffect
    reason_codes: tuple[str, ...]
    layers: tuple[LayerVote, ...] = Field(min_length=1)


class PolicyRequest(BaseModel):
    """One operation submitted for a vote. Arguments are evidence, not a rewrite."""

    model_config = ConfigDict(extra="forbid")

    operation: OperationKind
    agent: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PolicyPort(Protocol):
    """Local no-op or managed second vote."""

    def evaluate(self, request: PolicyRequest, local: LayerVote) -> PolicyDecision:
        """Return the effective decision for one operation."""


def combine(local: LayerVote, managed: LayerVote | None) -> PolicyDecision:
    """Conjunction of the local vote and, when present, the managed vote."""

    layers: list[LayerVote] = [local]
    reasons: list[str] = []
    denied = local.effect is PolicyEffect.DENY
    if local.effect is PolicyEffect.DENY:
        reasons.extend(local.reason_codes)
    if managed is not None:
        layers.append(managed)
        if managed.effect is PolicyEffect.DENY:
            denied = True
            reasons.extend(managed.reason_codes)
    return PolicyDecision(
        effect=PolicyEffect.DENY if denied else PolicyEffect.ALLOW,
        reason_codes=tuple(reasons),
        layers=tuple(layers),
    )


class LocalPolicyPort:
    """No managed vote and no network call. The local vote is the decision."""

    def evaluate(self, request: PolicyRequest, local: LayerVote) -> PolicyDecision:
        del request
        return combine(local, None)
