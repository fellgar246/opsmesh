"""Least privilege is the intersection of every layer that must allow an action."""

from opsmesh_api.identity.principal import AgentPrincipal


def allow(*, user: bool, agent: bool, tool: bool, policy: bool) -> bool:
    """Return whether user, agent, tool, and policy all allow the action."""

    return user and agent and tool and policy


def distinct_agent_identity(agent: str, configured: str, end_user_token: str | None) -> str:
    """Use the specialist identity. An end-user token is never the outbound credential."""

    identity = configured.strip() or f"opsmesh/{agent}"
    if end_user_token is not None and identity == end_user_token.strip():
        return f"opsmesh/{agent}"
    return identity


def agent_principal_for(agent: str, runtime_identity: str, tenant_id: str) -> AgentPrincipal:
    """Bind the specialist to the workflow tenant for this invocation."""

    return AgentPrincipal(
        agent_type=agent,
        runtime_identity=runtime_identity,
        tenant_id=tenant_id,
    )
