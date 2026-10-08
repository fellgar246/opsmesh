"""Tool handlers. They use the task tenant. Draft writes stay inside the simulator."""

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from opsmesh_api.forecasts.select import select_forecast_provider
from opsmesh_api.knowledge.policy_index import PolicySearch, product_policy_index, retrieval_query
from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.services.finance import FinanceService
from opsmesh_api.services.forecast import ForecastService
from opsmesh_api.services.inventory import InventoryService
from opsmesh_api.services.procurement import ProcurementService
from opsmesh_api.services.supplier import SupplierService
from opsmesh_api.settings import get_settings
from opsmesh_api.tools.policy_rules import RULE_SUPPLIER_NOT_APPROVED, approval_rule_ids
from opsmesh_api.tools.port import HandlerResult
from opsmesh_api.tools.schemas import (
    ApprovalRequirementArgs,
    DraftPurchaseOrderArgs,
    ForecastArgs,
    PolicySearchArgs,
    PurchaseImpactArgs,
    PurchaseOrderArgs,
    SkuRegionArgs,
    SubmitPurchaseOrderArgs,
    SupplierIdArgs,
    SupplierRestrictionArgs,
    SupplierTermsArgs,
    ToolArgs,
    validated_arguments_hash,
)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _now() -> datetime:
    return datetime.now(UTC)


def inventory_snapshot(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SkuRegionArgs)
    snapshot = InventoryService(db).get_snapshot(tenant_id, parsed.sku, parsed.region)
    return HandlerResult(
        source="inventory",
        observed_at=snapshot.observed_at,
        data=_dump(snapshot),
        result_reference=f"inventory:{parsed.sku}:{parsed.region}",
    )


def inventory_stockouts(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SkuRegionArgs)
    service = InventoryService(db)
    history = service.get_stockout_history(tenant_id, parsed.sku, parsed.region)
    parameters = service.get_reorder_parameters(tenant_id, parsed.sku, parsed.region)
    return HandlerResult(
        source="inventory",
        observed_at=parameters.observed_at,
        data=_dump(history),
        result_reference=f"inventory:{parsed.sku}:{parsed.region}:stockouts",
    )


def inventory_reorder_parameters(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SkuRegionArgs)
    parameters = InventoryService(db).get_reorder_parameters(tenant_id, parsed.sku, parsed.region)
    return HandlerResult(
        source="inventory",
        observed_at=parameters.observed_at,
        data=_dump(parameters),
        result_reference=f"inventory:{parsed.sku}:{parsed.region}:reorder-parameters",
    )


def forecast_get(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, ForecastArgs)
    reading = select_forecast_provider(db, get_settings()).read(
        tenant_id, parsed.sku, parsed.region, parsed.horizon_days
    )
    return HandlerResult(
        source=reading.provider,
        observed_at=reading.observed_at,
        data=reading.model_dump(mode="json"),
        result_reference=reading.source_record_id,
    )


def forecast_quality(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, ForecastArgs)
    quality = ForecastService(db).get_quality(
        tenant_id, parsed.sku, parsed.region, parsed.horizon_days
    )
    return HandlerResult(
        source="forecast",
        observed_at=quality.observed_at,
        data=_dump(quality),
        result_reference=f"forecast:{parsed.sku}:{parsed.region}:{parsed.horizon_days}:quality",
    )


def forecast_drivers(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, ForecastArgs)
    drivers = ForecastService(db).get_drivers(
        tenant_id, parsed.sku, parsed.region, parsed.horizon_days
    )
    return HandlerResult(
        source="forecast",
        observed_at=drivers.observed_at,
        data=_dump(drivers),
        result_reference=f"forecast:{parsed.sku}:{parsed.region}:{parsed.horizon_days}:drivers",
    )


def supplier_terms(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SupplierTermsArgs)
    terms = SupplierService(db).get_terms(tenant_id, parsed.supplier_id, parsed.sku)
    return HandlerResult(
        source="supplier",
        observed_at=_now(),
        data=_dump(terms),
        result_reference=f"supplier:{parsed.supplier_id}:{parsed.sku}",
    )


def supplier_reliability(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SupplierIdArgs)
    reliability = SupplierService(db).get_reliability(tenant_id, parsed.supplier_id)
    return HandlerResult(
        source="supplier",
        observed_at=_now(),
        data=_dump(reliability),
        result_reference=f"supplier:{parsed.supplier_id}:reliability",
    )


def supplier_open_pos(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SupplierIdArgs)
    orders = SupplierService(db).list_open_purchase_orders(tenant_id, parsed.supplier_id)
    return HandlerResult(
        source="supplier",
        observed_at=_now(),
        data=_dump(orders),
        result_reference=f"supplier:{parsed.supplier_id}:open-purchase-orders",
    )


def supplier_delay(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, SupplierIdArgs)
    delay = SupplierService(db).get_delay(tenant_id, parsed.supplier_id)
    return HandlerResult(
        source="supplier",
        observed_at=delay.started_at,
        data=_dump(delay),
        result_reference=f"supplier:{parsed.supplier_id}:delay",
    )


def finance_budget(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    _expect(args, ToolArgs)
    budget = FinanceService(db).get_budget(tenant_id)
    return HandlerResult(
        source="finance",
        observed_at=budget.observed_at,
        data=_dump(budget),
        result_reference=f"finance:{tenant_id}:budget",
    )


def finance_committed(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    _expect(args, ToolArgs)
    spend = FinanceService(db).get_committed_spend(tenant_id)
    return HandlerResult(
        source="finance",
        observed_at=spend.observed_at,
        data=_dump(spend),
        result_reference=f"finance:{tenant_id}:committed-spend",
    )


def finance_impact(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, PurchaseImpactArgs)
    impact = FinanceService(db).estimate(tenant_id, parsed.quantity, parsed.unit_cost_cents)
    return HandlerResult(
        source="finance",
        observed_at=_now(),
        data=_dump(impact),
        result_reference=f"finance:{tenant_id}:purchase-impact",
    )


def procurement_get(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    parsed = _expect(args, PurchaseOrderArgs)
    order = ProcurementService(db).get_purchase_order(tenant_id, parsed.purchase_order_id)
    return HandlerResult(
        source="procurement",
        observed_at=_now(),
        data=_dump(order),
        result_reference=f"procurement:{parsed.purchase_order_id}",
    )


def procurement_list(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    _expect(args, ToolArgs)
    orders = ProcurementService(db).list_purchase_orders(tenant_id)
    return HandlerResult(
        source="procurement",
        observed_at=_now(),
        data=_dump(orders),
        result_reference=f"procurement:{tenant_id}:purchase-orders",
    )


def policy_search(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    """Return citations for the task tenant. This payload has no approval flag."""

    del db
    parsed = _expect(args, PolicySearchArgs)
    query = parsed.query.strip() if isinstance(parsed.query, str) else ""
    if not query:
        query = retrieval_query(
            extended_cost_cents=parsed.extended_cost_cents,
            supplier_status=parsed.supplier_status,
            reason=None,
        )
    citations = product_policy_index().search(
        PolicySearch(
            tenant_id=tenant_id,
            query=query,
            as_of=parsed.as_of or date.today(),
            limit=parsed.max_passages,
        )
    )
    return HandlerResult(
        source="policy",
        observed_at=_now(),
        data={"citations": [item.as_dict() for item in citations]},
        result_reference="policy:search",
    )


def policy_approval(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    del db, tenant_id
    parsed = _expect(args, ApprovalRequirementArgs)
    requires_manager, requires_finance = approval_for_total(parsed.extended_cost_cents)
    return HandlerResult(
        source="policy",
        observed_at=_now(),
        data={
            "requires_manager_approval": requires_manager,
            "requires_finance_approval": requires_finance,
            "rule_ids": approval_rule_ids(parsed.extended_cost_cents),
        },
        result_reference="policy:approval-requirement",
    )


def procurement_create_draft(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    """Create one local draft. A matching replay returns the stored order."""

    parsed = _expect(args, DraftPurchaseOrderArgs)
    order = ProcurementService(db).create_draft_purchase_order(
        tenant_id,
        proposal_id=parsed.proposal_id,
        sku=parsed.sku,
        supplier_id=parsed.supplier_id,
        quantity=parsed.quantity,
        unit_cost_cents=parsed.unit_cost_cents,
        idempotency_key=parsed.idempotency_key,
        arguments_hash=validated_arguments_hash(parsed),
    )
    return HandlerResult(
        source="procurement",
        observed_at=_now(),
        data=_dump(order),
        result_reference=order.purchase_order_id,
    )


def procurement_submit_for_approval(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    """Advance a draft when required approvals match the live proposal hash."""

    parsed = _expect(args, SubmitPurchaseOrderArgs)
    order = ProcurementService(db).submit_purchase_order_for_approval(
        tenant_id,
        purchase_order_id=parsed.purchase_order_id,
        idempotency_key=parsed.idempotency_key,
        arguments_hash=validated_arguments_hash(parsed),
    )
    return HandlerResult(
        source="procurement",
        observed_at=_now(),
        data=_dump(order),
        result_reference=order.purchase_order_id,
    )


def policy_supplier(db: Session, tenant_id: str, args: BaseModel) -> HandlerResult:
    del db, tenant_id
    parsed = _expect(args, SupplierRestrictionArgs)
    restricted = parsed.supplier_status == "unapproved"
    return HandlerResult(
        source="policy",
        observed_at=_now(),
        data={
            "restricted": restricted,
            "rule_ids": [RULE_SUPPLIER_NOT_APPROVED] if restricted else [],
        },
        result_reference="policy:supplier-restriction",
    )


def _expect[T: BaseModel](args: BaseModel, model: type[T]) -> T:
    if isinstance(args, model):
        return args
    raise TypeError("Tool arguments do not match the registered schema.")
