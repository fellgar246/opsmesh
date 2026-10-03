"""Deterministic rule identifiers. These are not document citations."""

from opsmesh_api.operations.policy import approval_for_total

RULE_MANAGER_APPROVAL = "rule.purchase_above_5000_manager_approval"
RULE_FINANCE_APPROVAL = "rule.purchase_above_25000_finance_approval"
RULE_SUPPLIER_NOT_APPROVED = "rule.supplier_not_approved"


def approval_rule_ids(extended_cost_cents: int) -> list[str]:
    """Return approval rule identifiers for an extended cost in cents."""

    requires_manager, requires_finance = approval_for_total(extended_cost_cents)
    rule_ids: list[str] = []
    if requires_manager:
        rule_ids.append(RULE_MANAGER_APPROVAL)
    if requires_finance:
        rule_ids.append(RULE_FINANCE_APPROVAL)
    return rule_ids
