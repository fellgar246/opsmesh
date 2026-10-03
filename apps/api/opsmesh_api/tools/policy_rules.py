"""Deterministic rule identifiers. These are not document citations."""

from opsmesh_api.operations.policy import approval_for_total

RULE_MANAGER_APPROVAL = "rule.purchase_above_5000_manager_approval"
RULE_FINANCE_APPROVAL = "rule.purchase_above_25000_finance_approval"
RULE_SUPPLIER_NOT_APPROVED = "rule.supplier_not_approved"
RULE_BELOW_MINIMUM_ORDER = "rule.quantity_below_minimum_order_quantity"
RULE_CASE_PACK = "rule.quantity_not_multiple_of_case_pack"
RULE_BUDGET = "rule.extended_cost_exceeds_budget"


def approval_rule_ids(extended_cost_cents: int) -> list[str]:
    """Return approval rule identifiers for an extended cost in cents."""

    requires_manager, requires_finance = approval_for_total(extended_cost_cents)
    rule_ids: list[str] = []
    if requires_manager:
        rule_ids.append(RULE_MANAGER_APPROVAL)
    if requires_finance:
        rule_ids.append(RULE_FINANCE_APPROVAL)
    return rule_ids


def search_rule_ids(
    *,
    extended_cost_cents: int | None,
    supplier_status: str | None,
    quantity: int | None,
    minimum_order_quantity: int | None,
    case_pack: int | None,
    available_cents: int | None,
) -> list[str]:
    """Return rule identifiers that fire for the supplied facts."""

    rule_ids: list[str] = []
    if extended_cost_cents is not None:
        rule_ids.extend(approval_rule_ids(extended_cost_cents))
    if supplier_status == "unapproved":
        rule_ids.append(RULE_SUPPLIER_NOT_APPROVED)
    if (
        quantity is not None
        and minimum_order_quantity is not None
        and quantity < minimum_order_quantity
    ):
        rule_ids.append(RULE_BELOW_MINIMUM_ORDER)
    if quantity is not None and case_pack is not None and quantity % case_pack != 0:
        rule_ids.append(RULE_CASE_PACK)
    if (
        extended_cost_cents is not None
        and available_cents is not None
        and extended_cost_cents > available_cents
    ):
        rule_ids.append(RULE_BUDGET)
    return rule_ids
