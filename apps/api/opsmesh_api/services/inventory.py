"""Inventory reads. Inconsistent stock is reported and left unchanged."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.services.errors import NotFoundError, StockConflictError
from opsmesh_api.services.models import SkuStock, StockoutEvent, Tenant
from opsmesh_api.services.schemas import (
    InventorySnapshot,
    ReorderParameters,
    StockoutList,
    StockoutRead,
)


def _require_tenant(db: Session, tenant_id: str) -> None:
    if db.get(Tenant, tenant_id) is None:
        raise NotFoundError()


class InventoryService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_snapshot(self, tenant_id: str, sku: str, region: str) -> InventorySnapshot:
        stock = self._stock(tenant_id, sku, region)
        if stock.reserved > stock.on_hand:
            raise StockConflictError(on_hand=stock.on_hand, reserved=stock.reserved)
        return self._snapshot(stock)

    def get_reorder_parameters(self, tenant_id: str, sku: str, region: str) -> ReorderParameters:
        stock = self._stock(tenant_id, sku, region)
        return ReorderParameters(
            tenant_id=stock.tenant_id,
            sku=stock.sku,
            region=stock.region,
            reorder_point_units=stock.reorder_point,
            safety_stock_units=stock.safety_stock,
            observed_at=stock.observed_at,
        )

    def list_snapshots_for_sku(self, tenant_id: str, sku: str) -> list[InventorySnapshot]:
        _require_tenant(self._db, tenant_id)
        rows = self._db.scalars(
            select(SkuStock)
            .where(SkuStock.tenant_id == tenant_id, SkuStock.sku == sku)
            .order_by(SkuStock.region)
        ).all()
        snapshots: list[InventorySnapshot] = []
        for stock in rows:
            if stock.reserved > stock.on_hand:
                raise StockConflictError(on_hand=stock.on_hand, reserved=stock.reserved)
            snapshots.append(self._snapshot(stock))
        return snapshots

    def get_stockout_history(self, tenant_id: str, sku: str, region: str) -> StockoutList:
        self._stock(tenant_id, sku, region)
        rows = self._db.scalars(
            select(StockoutEvent)
            .where(
                StockoutEvent.tenant_id == tenant_id,
                StockoutEvent.sku == sku,
                StockoutEvent.region == region,
            )
            .order_by(StockoutEvent.started_at, StockoutEvent.stockout_id)
        ).all()
        return StockoutList(
            stockouts=[
                StockoutRead(
                    stockout_id=row.stockout_id,
                    tenant_id=row.tenant_id,
                    sku=row.sku,
                    region=row.region,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    units_short=row.units_short,
                )
                for row in rows
            ]
        )

    def _snapshot(self, stock: SkuStock) -> InventorySnapshot:
        return InventorySnapshot(
            tenant_id=stock.tenant_id,
            sku=stock.sku,
            region=stock.region,
            on_hand_units=stock.on_hand,
            reserved_units=stock.reserved,
            available_units=stock.available,
            reorder_point_units=stock.reorder_point,
            observed_at=stock.observed_at,
        )

    def _stock(self, tenant_id: str, sku: str, region: str) -> SkuStock:
        _require_tenant(self._db, tenant_id)
        stock = self._db.get(SkuStock, (tenant_id, sku, region))
        if stock is None:
            raise NotFoundError()
        return stock
