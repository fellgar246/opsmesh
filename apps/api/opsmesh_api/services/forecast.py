"""Forecast fixture reads. Values come from stored fixtures, not a live model."""

from sqlalchemy.orm import Session

from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import Forecast, Tenant, Trend
from opsmesh_api.services.schemas import (
    DemandDriversRead,
    ForecastQualityRead,
    ForecastRead,
    PromotionContext,
    SeasonalityContext,
)


class ForecastService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_forecast(
        self, tenant_id: str, sku: str, region: str, horizon_days: int
    ) -> ForecastRead:
        row = self._row(tenant_id, sku, region, horizon_days)
        return ForecastRead(
            tenant_id=row.tenant_id,
            sku=row.sku,
            region=row.region,
            horizon_days=row.horizon_days,
            p10=row.p10,
            p50=row.p50,
            p90=row.p90,
            trend=Trend(row.trend),
            quality=row.quality,
            seasonality=SeasonalityContext(
                label=row.seasonality_label, index=row.seasonality_index
            ),
            promotion=PromotionContext(active=row.promotion_active, name=row.promotion_name),
            observed_at=row.observed_at,
        )

    def get_quality(
        self, tenant_id: str, sku: str, region: str, horizon_days: int
    ) -> ForecastQualityRead:
        row = self._row(tenant_id, sku, region, horizon_days)
        return ForecastQualityRead(
            tenant_id=row.tenant_id,
            sku=row.sku,
            region=row.region,
            horizon_days=row.horizon_days,
            quality=row.quality,
            observed_at=row.observed_at,
        )

    def get_drivers(
        self, tenant_id: str, sku: str, region: str, horizon_days: int
    ) -> DemandDriversRead:
        row = self._row(tenant_id, sku, region, horizon_days)
        return DemandDriversRead(
            tenant_id=row.tenant_id,
            sku=row.sku,
            region=row.region,
            horizon_days=row.horizon_days,
            trend=Trend(row.trend),
            seasonality=SeasonalityContext(
                label=row.seasonality_label, index=row.seasonality_index
            ),
            promotion=PromotionContext(active=row.promotion_active, name=row.promotion_name),
            observed_at=row.observed_at,
        )

    def _row(self, tenant_id: str, sku: str, region: str, horizon_days: int) -> Forecast:
        if self._db.get(Tenant, tenant_id) is None:
            raise NotFoundError()
        row = self._db.get(Forecast, (tenant_id, sku, region, horizon_days))
        if row is None:
            raise NotFoundError()
        return row
