"""Skip specialists when deterministic facts already decide a reorder.

Budget plans and supplier-delay reviews still run finance or supplier,
because those workflow types need them. This helper applies only to reorder.
"""

from typing import Any

from opsmesh_api.operations.quantity import select_demand

SKIP_REASON = "adequate_stock"
SKIPPED_AGENTS = frozenset({"supplier", "finance"})


def reorder_skip_reason(
    *,
    service_level: float,
    inventory: dict[str, Any] | None,
    demand: dict[str, Any] | None,
) -> str | None:
    """Return a skip reason when stock already covers the selected forecast."""

    if not isinstance(inventory, dict) or not isinstance(demand, dict):
        return None
    available = _whole(inventory.get("available_units"))
    p50 = _whole(demand.get("p50"))
    p90 = _whole(demand.get("p90"))
    if available is None or p50 is None or p90 is None:
        return None
    _quantile, selected = select_demand(service_level, p50, p90)
    if available > selected:
        return SKIP_REASON
    return None


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
