"""Read-only tool catalog. Names and allowlists stay stable for later gateways."""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.tools import handlers
from opsmesh_api.tools.port import ToolHandler
from opsmesh_api.tools.schemas import (
    ApprovalRequirementArgs,
    ForecastArgs,
    PolicySearchArgs,
    PurchaseImpactArgs,
    PurchaseOrderArgs,
    SkuRegionArgs,
    SupplierIdArgs,
    SupplierRestrictionArgs,
    SupplierTermsArgs,
    ToolArgs,
)

_INVENTORY = frozenset({AgentType.INVENTORY})
_DEMAND = frozenset({AgentType.DEMAND})
_SUPPLIER = frozenset({AgentType.SUPPLIER})
_FINANCE = frozenset({AgentType.FINANCE})
_POLICY = frozenset({AgentType.POLICY})


@dataclass(frozen=True)
class ToolSpec:
    """One registered read tool before it is sealed into the registry."""

    name: str
    argument_model: type[BaseModel]
    permitted_agents: frozenset[AgentType]
    handler: ToolHandler
    kind: str = "read"
    read_only: bool = True


READ_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "inventory.get_snapshot",
        SkuRegionArgs,
        _INVENTORY,
        handlers.inventory_snapshot,
    ),
    ToolSpec(
        "inventory.get_stockout_history",
        SkuRegionArgs,
        _INVENTORY,
        handlers.inventory_stockouts,
    ),
    ToolSpec(
        "inventory.get_reorder_parameters",
        SkuRegionArgs,
        _INVENTORY,
        handlers.inventory_reorder_parameters,
    ),
    ToolSpec(
        "forecast.get_forecast",
        ForecastArgs,
        _DEMAND,
        handlers.forecast_get,
    ),
    ToolSpec(
        "forecast.get_forecast_quality",
        ForecastArgs,
        _DEMAND,
        handlers.forecast_quality,
    ),
    ToolSpec(
        "forecast.get_demand_drivers",
        ForecastArgs,
        _DEMAND,
        handlers.forecast_drivers,
    ),
    ToolSpec(
        "supplier.get_terms",
        SupplierTermsArgs,
        _SUPPLIER,
        handlers.supplier_terms,
    ),
    ToolSpec(
        "supplier.get_reliability",
        SupplierIdArgs,
        _SUPPLIER,
        handlers.supplier_reliability,
    ),
    ToolSpec(
        "supplier.list_open_pos",
        SupplierIdArgs,
        _SUPPLIER,
        handlers.supplier_open_pos,
    ),
    ToolSpec(
        "supplier.get_delay",
        SupplierIdArgs,
        _SUPPLIER,
        handlers.supplier_delay,
    ),
    ToolSpec(
        "finance.get_budget",
        ToolArgs,
        _FINANCE,
        handlers.finance_budget,
    ),
    ToolSpec(
        "finance.get_committed_spend",
        ToolArgs,
        _FINANCE,
        handlers.finance_committed,
    ),
    ToolSpec(
        "finance.estimate_purchase_impact",
        PurchaseImpactArgs,
        _FINANCE,
        handlers.finance_impact,
    ),
    ToolSpec(
        "procurement.get_purchase_order",
        PurchaseOrderArgs,
        _SUPPLIER,
        handlers.procurement_get,
    ),
    ToolSpec(
        "procurement.list_purchase_orders",
        ToolArgs,
        _SUPPLIER,
        handlers.procurement_list,
    ),
    ToolSpec(
        "policy.search",
        PolicySearchArgs,
        _POLICY,
        handlers.policy_search,
    ),
    ToolSpec(
        "policy.check_approval_requirement",
        ApprovalRequirementArgs,
        _POLICY,
        handlers.policy_approval,
    ),
    ToolSpec(
        "policy.check_supplier_restriction",
        SupplierRestrictionArgs,
        _POLICY,
        handlers.policy_supplier,
    ),
)


def permitted_tool_names(
    agent: AgentType, specs: Sequence[ToolSpec] | None = None
) -> tuple[str, ...]:
    """Return the sorted tool names granted to one agent."""

    chosen = READ_TOOL_SPECS if specs is None else specs
    return tuple(sorted(spec.name for spec in chosen if agent in spec.permitted_agents))
