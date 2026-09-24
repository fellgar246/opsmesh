"""Budget reads and a pure purchase-impact estimate."""

from sqlalchemy.orm import Session

from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import Budget, Tenant
from opsmesh_api.services.schemas import BudgetRead, CommittedSpendRead, PurchaseImpactRead


def estimate_purchase_impact(quantity: int, unit_cost_cents: int) -> int:
    """Return extended cost in cents. No I/O and no stored state."""

    if quantity < 0 or unit_cost_cents < 0:
        raise ValueError("quantity and unit cost must be non-negative")
    return quantity * unit_cost_cents


class FinanceService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_budget(self, tenant_id: str) -> BudgetRead:
        budget = self._budget(tenant_id)
        return BudgetRead(
            tenant_id=budget.tenant_id,
            available_cents=budget.available_cents,
            committed_cents=budget.committed_cents,
            observed_at=budget.observed_at,
        )

    def get_committed_spend(self, tenant_id: str) -> CommittedSpendRead:
        budget = self._budget(tenant_id)
        return CommittedSpendRead(
            tenant_id=budget.tenant_id,
            committed_cents=budget.committed_cents,
            observed_at=budget.observed_at,
        )

    def estimate(self, tenant_id: str, quantity: int, unit_cost_cents: int) -> PurchaseImpactRead:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()
        extended = estimate_purchase_impact(quantity, unit_cost_cents)
        return PurchaseImpactRead(
            tenant_id=tenant_id,
            quantity=quantity,
            unit_cost_cents=unit_cost_cents,
            extended_cost_cents=extended,
        )

    def _budget(self, tenant_id: str) -> Budget:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()
        budget = self._db.get(Budget, tenant_id)
        if budget is None:
            raise NotFoundError()
        return budget
