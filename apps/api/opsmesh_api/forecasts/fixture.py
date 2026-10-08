"""Fixture forecast provider. Values come from the local simulator store."""

from sqlalchemy.orm import Session

from opsmesh_api.forecasts.port import PROVIDER_FIXTURE, ForecastReading
from opsmesh_api.services.forecast import ForecastService


class FixtureForecastProvider:
    """Read the seeded quantiles. This is the provider used unless another is selected."""

    def __init__(self, db: Session) -> None:
        self._forecasts = ForecastService(db)

    def read(self, tenant_id: str, sku: str, region: str, horizon_days: int) -> ForecastReading:
        row = self._forecasts.get_forecast(tenant_id, sku, region, horizon_days)
        return ForecastReading(
            tenant_id=row.tenant_id,
            sku=row.sku,
            region=row.region,
            horizon_days=row.horizon_days,
            p10=row.p10,
            p50=row.p50,
            p90=row.p90,
            quality=row.quality,
            observed_at=row.observed_at,
            provider=PROVIDER_FIXTURE,
            source_record_id=f"{row.sku}:{row.region}:{row.horizon_days}",
        )
