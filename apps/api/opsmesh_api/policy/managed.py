"""Managed policy adapter. The AgentCore client stays in this module.

The local engine does not import it. A failed or missing decision denies the
operation. An allow from this adapter cannot overturn a local deny.
"""

import importlib
from threading import Lock
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict

from opsmesh_api.policy.port import (
    REASON_MANAGED_DENIED,
    REASON_MANAGED_UNAVAILABLE,
    LayerVote,
    LocalPolicyPort,
    PolicyDecision,
    PolicyEffect,
    PolicyLayer,
    PolicyRequest,
    combine,
)
from opsmesh_api.settings import Settings

_UNAVAILABLE = "The policy engine did not return a decision."


class ManagedPolicyUnavailable(Exception):
    """The managed adapter could not produce a vote."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ManagedVote(BaseModel):
    """Second vote. Extra fields, including a quantity, are rejected."""

    model_config = ConfigDict(extra="forbid")

    effect: PolicyEffect
    reason_codes: tuple[str, ...] = ()


class ManagedPolicyTransport(Protocol):
    """Port for one managed vote. Tests pass a fake. The live client implements it."""

    def evaluate(self, request: PolicyRequest) -> ManagedVote:
        """Return the managed allow or deny for one operation."""


class _PolicyControl(Protocol):
    def get_policy_engine(self, **kwargs: Any) -> dict[str, Any]:
        """Read one AgentCore policy engine."""

    def authorize_action(self, **kwargs: Any) -> dict[str, Any]:
        """Authorize one agent or tool action against that engine."""


class ManagedPolicyAdapter:
    """Optional second vote. It records both layers and does not edit arguments."""

    def __init__(self, transport: ManagedPolicyTransport) -> None:
        self._transport = transport
        self._lock = Lock()
        self.calls = 0

    def evaluate(self, request: PolicyRequest, local: LayerVote) -> PolicyDecision:
        isolated = request.model_copy(update={"arguments": dict(request.arguments)})
        try:
            with self._lock:
                self.calls += 1
            vote = self._transport.evaluate(isolated)
        except Exception:
            return combine(local, _unavailable_vote())
        return combine(local, _managed_layer(vote))


class AgentCorePolicyClient:
    """Amazon Bedrock AgentCore Policy client. SDK imports stay in this class."""

    def __init__(
        self,
        engine_id: str,
        *,
        region: str,
        client: _PolicyControl | None = None,
    ) -> None:
        self._engine_id = engine_id.strip()
        self._region = region
        self._client = client

    def evaluate(self, request: PolicyRequest) -> ManagedVote:
        if not self._engine_id:
            raise ManagedPolicyUnavailable("The policy engine is not configured.")
        sdk = self._sdk()
        try:
            engine = sdk.get_policy_engine(policyEngineId=self._engine_id)
        except ManagedPolicyUnavailable:
            raise
        except Exception as exc:
            raise ManagedPolicyUnavailable(_UNAVAILABLE) from exc
        if not isinstance(engine, dict) or engine.get("status") != "ACTIVE":
            raise ManagedPolicyUnavailable("The policy engine is not active.")
        authorizer = getattr(sdk, "authorize_action", None)
        if not callable(authorizer):
            raise ManagedPolicyUnavailable(_UNAVAILABLE)
        try:
            response = authorizer(
                policyEngineId=self._engine_id,
                operation=request.operation.value,
                action=request.action,
                agent=request.agent,
                tenantId=request.tenant_id,
                arguments=dict(request.arguments),
            )
        except ManagedPolicyUnavailable:
            raise
        except Exception as exc:
            raise ManagedPolicyUnavailable(_UNAVAILABLE) from exc
        return _vote_from_response(response)

    def _sdk(self) -> _PolicyControl:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("boto3")
        except ImportError as exc:
            raise ManagedPolicyUnavailable("The policy engine client is not installed.") from exc
        factory = getattr(module, "client", None)
        if not callable(factory):
            raise ManagedPolicyUnavailable("The policy engine client is not installed.")
        return cast(
            _PolicyControl,
            factory("bedrock-agentcore-control", region_name=self._region),
        )


def select_policy_port(
    settings: Settings,
    transport: ManagedPolicyTransport | None = None,
) -> LocalPolicyPort | ManagedPolicyAdapter:
    """Use the local port unless the managed policy flag is actually on."""

    if not settings.cloud_capability(settings.cloud_policy_enabled):
        return LocalPolicyPort()
    resolved = transport
    if resolved is None:
        resolved = AgentCorePolicyClient(
            settings.policy_engine_id,
            region=settings.bedrock_region,
        )
    return ManagedPolicyAdapter(resolved)


def _managed_layer(vote: ManagedVote) -> LayerVote:
    if vote.effect is PolicyEffect.DENY:
        codes = vote.reason_codes or (REASON_MANAGED_DENIED,)
        return LayerVote(layer=PolicyLayer.MANAGED, effect=PolicyEffect.DENY, reason_codes=codes)
    return LayerVote(layer=PolicyLayer.MANAGED, effect=PolicyEffect.ALLOW)


def _unavailable_vote() -> LayerVote:
    return LayerVote(
        layer=PolicyLayer.MANAGED,
        effect=PolicyEffect.DENY,
        reason_codes=(REASON_MANAGED_UNAVAILABLE,),
    )


def _vote_from_response(response: object) -> ManagedVote:
    if not isinstance(response, dict):
        raise ManagedPolicyUnavailable(_UNAVAILABLE)
    decision = response.get("decision")
    if decision == "ALLOW":
        return ManagedVote(effect=PolicyEffect.ALLOW)
    if decision == "DENY":
        raw = response.get("reasonCodes", ())
        codes = (
            tuple(item for item in raw if isinstance(item, str) and item)
            if isinstance(raw, list)
            else ()
        )
        return ManagedVote(
            effect=PolicyEffect.DENY,
            reason_codes=codes or (REASON_MANAGED_DENIED,),
        )
    raise ManagedPolicyUnavailable(_UNAVAILABLE)
