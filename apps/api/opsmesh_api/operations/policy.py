"""Purchase approval rules. Amounts are integer cents.

Thresholds are stated in major currency units and compared after converting
to cents: a total above 5000 requires manager approval, and a total above
25000 also requires finance approval. Manager approval still applies when
finance approval applies. A total equal to a threshold does not cross it.
"""

MANAGER_APPROVAL_CENTS = 500_000
FINANCE_APPROVAL_CENTS = 2_500_000

REASON_ADEQUATE_STOCK = "adequate_stock"
REASON_INSUFFICIENT_BUDGET = "insufficient_budget"
REASON_SUPPLIER_NOT_APPROVED = "supplier_not_approved"
REASON_MISSING_EVIDENCE = "missing_evidence"


def approval_for_total(extended_cost_cents: int) -> tuple[bool, bool]:
    """Return manager and finance approval flags for an extended cost."""

    requires_finance = extended_cost_cents > FINANCE_APPROVAL_CENTS
    requires_manager = extended_cost_cents > MANAGER_APPROVAL_CENTS
    return requires_manager, requires_finance
