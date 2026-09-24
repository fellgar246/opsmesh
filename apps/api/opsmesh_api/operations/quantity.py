"""Reorder quantity from demand, stock, case pack, and budget."""

from opsmesh_api.operations.policy import (
    REASON_ADEQUATE_STOCK,
    REASON_INSUFFICIENT_BUDGET,
    REASON_SUPPLIER_NOT_APPROVED,
)
from opsmesh_api.services.models import SupplierStatus

P90_SERVICE_LEVEL = 0.90


def case_pack_size(case_pack: int | None) -> int:
    """A missing or non-positive case pack is one unit."""

    if case_pack is None or case_pack < 1:
        return 1
    return case_pack


def round_up_to_pack(quantity: int, case_pack: int | None) -> int:
    pack = case_pack_size(case_pack)
    return ((quantity + pack - 1) // pack) * pack


def round_down_to_pack(quantity: int, case_pack: int | None) -> int:
    pack = case_pack_size(case_pack)
    if quantity <= 0:
        return 0
    return (quantity // pack) * pack


def select_demand(service_level: float, p50: int, p90: int) -> tuple[str, int]:
    """Service level of 0.90 or higher selects P90. Anything lower selects P50."""

    if service_level >= P90_SERVICE_LEVEL:
        return "p90", p90
    return "p50", p50


def rounded_need(
    available_units: int,
    selected_demand: int,
    case_pack: int | None,
    minimum_order_quantity: int,
) -> tuple[int, int]:
    """Return gross need and the packed order quantity.

    Non-positive need yields quantity 0. A positive need is rounded up to the
    case pack. If that quantity is still below the minimum order, it is raised
    to the minimum and rounded up to the case pack again.
    """

    gross_need = selected_demand - available_units
    if gross_need <= 0:
        return gross_need, 0
    quantity = round_up_to_pack(gross_need, case_pack)
    if quantity < minimum_order_quantity:
        quantity = round_up_to_pack(minimum_order_quantity, case_pack)
    return gross_need, quantity


def affordable_units(
    available_cents: int, unit_cost_cents: int, case_pack: int | None
) -> int | None:
    """Budget divided by unit cost, rounded down to a whole case pack.

    A zero unit cost returns None, meaning every quantity is affordable.
    """

    if unit_cost_cents < 0 or available_cents < 0:
        raise ValueError("budget and unit cost must be non-negative")
    if unit_cost_cents == 0:
        return None
    return round_down_to_pack(available_cents // unit_cost_cents, case_pack)


class ReorderMath:
    """Feasible quantity, or a machine-readable reason not to buy."""

    def __init__(
        self,
        *,
        selected_quantile: str,
        selected_demand: int,
        gross_need: int,
        quantity: int,
        unit_cost_cents: int,
        extended_cost_cents: int,
        reason: str | None,
        blocked: bool,
    ) -> None:
        self.selected_quantile = selected_quantile
        self.selected_demand = selected_demand
        self.gross_need = gross_need
        self.quantity = quantity
        self.unit_cost_cents = unit_cost_cents
        self.extended_cost_cents = extended_cost_cents
        self.reason = reason
        self.blocked = blocked

    @property
    def feasible(self) -> bool:
        return self.reason is None


def solve_reorder(
    *,
    service_level: float,
    p50: int,
    p90: int,
    available_units: int,
    case_pack: int | None,
    minimum_order_quantity: int,
    unit_cost_cents: int,
    supplier_status: str,
    available_cents: int,
) -> ReorderMath:
    """Apply the reorder rule sequence and stop at the first blocking rule."""

    quantile, selected = select_demand(service_level, p50, p90)
    gross_need, quantity = rounded_need(
        available_units, selected, case_pack, minimum_order_quantity
    )
    if supplier_status != SupplierStatus.APPROVED.value:
        return ReorderMath(
            selected_quantile=quantile,
            selected_demand=selected,
            gross_need=gross_need,
            quantity=0,
            unit_cost_cents=unit_cost_cents,
            extended_cost_cents=0,
            reason=REASON_SUPPLIER_NOT_APPROVED,
            blocked=True,
        )
    if quantity == 0:
        return ReorderMath(
            selected_quantile=quantile,
            selected_demand=selected,
            gross_need=gross_need,
            quantity=0,
            unit_cost_cents=unit_cost_cents,
            extended_cost_cents=0,
            reason=REASON_ADEQUATE_STOCK,
            blocked=False,
        )
    cap = affordable_units(available_cents, unit_cost_cents, case_pack)
    pack = case_pack_size(case_pack)
    if cap is None:
        cap = quantity
    if cap < minimum_order_quantity:
        return ReorderMath(
            selected_quantile=quantile,
            selected_demand=selected,
            gross_need=gross_need,
            quantity=0,
            unit_cost_cents=unit_cost_cents,
            extended_cost_cents=0,
            reason=REASON_INSUFFICIENT_BUDGET,
            blocked=False,
        )
    feasible = min(quantity, cap)
    if feasible < minimum_order_quantity or feasible % pack != 0:
        return ReorderMath(
            selected_quantile=quantile,
            selected_demand=selected,
            gross_need=gross_need,
            quantity=0,
            unit_cost_cents=unit_cost_cents,
            extended_cost_cents=0,
            reason=REASON_INSUFFICIENT_BUDGET,
            blocked=False,
        )
    return ReorderMath(
        selected_quantile=quantile,
        selected_demand=selected,
        gross_need=gross_need,
        quantity=feasible,
        unit_cost_cents=unit_cost_cents,
        extended_cost_cents=feasible * unit_cost_cents,
        reason=None,
        blocked=False,
    )
