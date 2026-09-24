"""Idempotent loader for local enterprise fixtures."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from opsmesh_api.db import SessionLocal
from opsmesh_api.logging import configure_logging
from opsmesh_api.services.errors import FixtureError
from opsmesh_api.services.models import (
    Budget,
    Forecast,
    PurchaseOrder,
    SkuStock,
    StockoutEvent,
    Supplier,
    SupplierDelay,
    SupplierStatus,
    SupplierTerms,
    Tenant,
)
from opsmesh_api.services.schemas import FixtureDocument

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "enterprise.json"

_MONEY_KEYS = frozenset({"available_cents", "committed_cents", "unit_cost_cents"})
_SCORE_KEYS = frozenset({"quality", "reliability_score"})

_CLEAR_ORDER = (
    PurchaseOrder,
    SupplierDelay,
    SupplierTerms,
    StockoutEvent,
    Forecast,
    SkuStock,
    Supplier,
    Budget,
    Tenant,
)


def parse_fixtures(raw: dict[str, Any]) -> FixtureDocument:
    """Validate a fixture document. Invalid documents never reach the database."""

    _reject_bad_scalars(raw, "$")
    try:
        document = FixtureDocument.model_validate(raw)
    except ValidationError as exc:
        raise FixtureError(str(exc)) from exc
    _reject_broken_references(document)
    return document


def load_fixtures(session: Session, document: dict[str, Any] | None = None) -> None:
    """Replace simulator rows with fixtures. A second load reproduces the first."""

    raw = document if document is not None else json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    parsed = parse_fixtures(raw)
    try:
        for model in _CLEAR_ORDER:
            session.execute(delete(model))
        _insert(session, parsed)
        session.commit()
    except Exception:
        session.rollback()
        raise


def _score_is_invalid(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return True
    return not 0 <= float(value) <= 1


def _reject_bad_scalars(node: object, path: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            loc = f"{path}.{key}"
            if key in _MONEY_KEYS and type(value) is not int:
                raise FixtureError(f"{loc} must be integer cents")
            if key in _SCORE_KEYS and _score_is_invalid(value):
                raise FixtureError(f"{loc} must be a number from 0 to 1")
            _reject_bad_scalars(value, loc)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_bad_scalars(item, f"{path}[{index}]")


def _reject_broken_references(document: FixtureDocument) -> None:
    tenant_ids = [tenant.tenant_id for tenant in document.tenants]
    if len(tenant_ids) != len(set(tenant_ids)):
        raise FixtureError("duplicate tenant_id")
    if not any(
        supplier.status == SupplierStatus.UNAPPROVED
        for tenant in document.tenants
        for supplier in tenant.suppliers
    ):
        raise FixtureError("at least one supplier must be unapproved")

    purchase_order_ids: list[str] = []
    for tenant in document.tenants:
        supplier_ids = [supplier.supplier_id for supplier in tenant.suppliers]
        if len(supplier_ids) != len(set(supplier_ids)):
            raise FixtureError(f"duplicate supplier_id in {tenant.tenant_id}")
        preferred: set[str] = set()
        for supplier in tenant.suppliers:
            for terms in supplier.terms:
                if not terms.preferred:
                    continue
                if terms.sku in preferred:
                    raise FixtureError(f"multiple preferred suppliers for {terms.sku}")
                preferred.add(terms.sku)
        stock_keys = {(stock.sku, stock.region) for stock in tenant.stocks}
        if len(stock_keys) != len(tenant.stocks):
            raise FixtureError(f"duplicate stock in {tenant.tenant_id}")
        forecast_keys = {
            (forecast.sku, forecast.region, forecast.horizon_days) for forecast in tenant.forecasts
        }
        if len(forecast_keys) != len(tenant.forecasts):
            raise FixtureError(f"duplicate forecast in {tenant.tenant_id}")
        for stockout in tenant.stockouts:
            if (stockout.sku, stockout.region) not in stock_keys:
                raise FixtureError(f"stockout {stockout.stockout_id} has no matching stock")
        known_suppliers = set(supplier_ids)
        for order in tenant.purchase_orders:
            if order.supplier_id not in known_suppliers:
                raise FixtureError(f"purchase order {order.purchase_order_id} supplier is missing")
            purchase_order_ids.append(order.purchase_order_id)
    if len(purchase_order_ids) != len(set(purchase_order_ids)):
        raise FixtureError("duplicate purchase order id")


def _insert(session: Session, document: FixtureDocument) -> None:
    observed_at = document.observed_at
    for tenant in document.tenants:
        session.add(Tenant(tenant_id=tenant.tenant_id, name=tenant.name))
    session.flush()
    for tenant in document.tenants:
        session.add(
            Budget(
                tenant_id=tenant.tenant_id,
                available_cents=tenant.budget.available_cents,
                committed_cents=tenant.budget.committed_cents,
                observed_at=observed_at,
            )
        )
        for supplier in tenant.suppliers:
            session.add(
                Supplier(
                    tenant_id=tenant.tenant_id,
                    supplier_id=supplier.supplier_id,
                    name=supplier.name,
                    status=supplier.status.value,
                    reliability_score=float(supplier.reliability_score),
                )
            )
    session.flush()
    for tenant in document.tenants:
        for supplier in tenant.suppliers:
            for terms in supplier.terms:
                session.add(
                    SupplierTerms(
                        tenant_id=tenant.tenant_id,
                        supplier_id=supplier.supplier_id,
                        sku=terms.sku,
                        lead_time_days=terms.lead_time_days,
                        minimum_order_quantity=terms.minimum_order_quantity,
                        case_pack=terms.case_pack,
                        unit_cost_cents=terms.unit_cost_cents,
                        preferred=terms.preferred,
                    )
                )
            if supplier.delay is not None:
                session.add(
                    SupplierDelay(
                        delay_id=supplier.delay.delay_id,
                        tenant_id=tenant.tenant_id,
                        supplier_id=supplier.supplier_id,
                        active=supplier.delay.active,
                        delay_days=supplier.delay.delay_days,
                        reason=supplier.delay.reason,
                        started_at=supplier.delay.started_at,
                    )
                )
        for stock in tenant.stocks:
            session.add(
                SkuStock(
                    tenant_id=tenant.tenant_id,
                    sku=stock.sku,
                    region=stock.region,
                    on_hand=stock.on_hand,
                    reserved=stock.reserved,
                    available=stock.available,
                    reorder_point=stock.reorder_point,
                    safety_stock=stock.safety_stock,
                    observed_at=observed_at,
                )
            )
        for stockout in tenant.stockouts:
            session.add(
                StockoutEvent(
                    stockout_id=stockout.stockout_id,
                    tenant_id=tenant.tenant_id,
                    sku=stockout.sku,
                    region=stockout.region,
                    started_at=stockout.started_at,
                    ended_at=stockout.ended_at,
                    units_short=stockout.units_short,
                )
            )
        for forecast in tenant.forecasts:
            session.add(
                Forecast(
                    tenant_id=tenant.tenant_id,
                    sku=forecast.sku,
                    region=forecast.region,
                    horizon_days=forecast.horizon_days,
                    p10=forecast.p10,
                    p50=forecast.p50,
                    p90=forecast.p90,
                    trend=forecast.trend.value,
                    quality=float(forecast.quality),
                    seasonality_label=forecast.seasonality.label,
                    seasonality_index=float(forecast.seasonality.index),
                    promotion_active=forecast.promotion.active,
                    promotion_name=forecast.promotion.name,
                    observed_at=observed_at,
                )
            )
        for order in tenant.purchase_orders:
            session.add(
                PurchaseOrder(
                    purchase_order_id=order.purchase_order_id,
                    tenant_id=tenant.tenant_id,
                    supplier_id=order.supplier_id,
                    sku=order.sku,
                    quantity=order.quantity,
                    status=order.status.value,
                    expected_receipt_on=order.expected_receipt_on,
                )
            )


def main() -> None:
    configure_logging()
    session = SessionLocal()
    try:
        load_fixtures(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
