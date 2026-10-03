"""Prompt versions and the tools each specialist is allowed to see."""

from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.tools.catalog import permitted_tool_names

# Specialists are introduced in this order. An empty allowlist means this full set.
CONVERSION_ORDER: tuple[AgentType, ...] = (
    AgentType.POLICY,
    AgentType.INVENTORY,
    AgentType.SUPPLIER,
    AgentType.FINANCE,
    AgentType.DEMAND,
)

PERMITTED_TOOLS: dict[AgentType, tuple[str, ...]] = {
    agent: permitted_tool_names(agent) for agent in CONVERSION_ORDER
}

PROMPT_VERSIONS: dict[AgentType, str] = {
    AgentType.POLICY: "policy-specialist-1",
    AgentType.INVENTORY: "inventory-specialist-1",
    AgentType.SUPPLIER: "supplier-specialist-1",
    AgentType.FINANCE: "finance-specialist-1",
    AgentType.DEMAND: "demand-specialist-1",
}

_SHARED_RULE = (
    "Return one JSON result envelope and nothing else. "
    "Stock, price, budget, lead time, and approval booleans come from tool results. "
    "Do not invent those values."
)

SYSTEM_PROMPTS: dict[AgentType, str] = {
    AgentType.POLICY: (
        "You are the policy specialist. Explain the deterministic policy decision. "
        + _SHARED_RULE
    ),
    AgentType.INVENTORY: (
        "You are the inventory specialist. Explain the inventory snapshot. " + _SHARED_RULE
    ),
    AgentType.SUPPLIER: (
        "You are the supplier specialist. Explain the supplier terms. " + _SHARED_RULE
    ),
    AgentType.FINANCE: (
        "You are the finance specialist. Explain the purchasing budget. " + _SHARED_RULE
    ),
    AgentType.DEMAND: (
        "You are the demand specialist. Explain the forecast quantiles. " + _SHARED_RULE
    ),
}
