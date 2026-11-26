"""Forecast fixture routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.services.forecast import ForecastService
from opsmesh_api.services.schemas import DemandDriversRead, ForecastQualityRead, ForecastRead

router = APIRouter(prefix="/tenants/{tenant_id}/forecasts", tags=["forecast"])


def _service(db: Annotated[Session, Depends(get_db)]) -> ForecastService:
    return ForecastService(db)


@router.get("/{sku}/{region}", response_model=ForecastRead)
def forecast(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[ForecastService, Depends(_service)],
    horizon_days: Annotated[int, Query(ge=1)] = 30,
) -> ForecastRead:
    return call(lambda: service.get_forecast(tenant_id, sku, region, horizon_days))


@router.get("/{sku}/{region}/quality", response_model=ForecastQualityRead)
def forecast_quality(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[ForecastService, Depends(_service)],
    horizon_days: Annotated[int, Query(ge=1)] = 30,
) -> ForecastQualityRead:
    return call(lambda: service.get_quality(tenant_id, sku, region, horizon_days))


@router.get("/{sku}/{region}/drivers", response_model=DemandDriversRead)
def demand_drivers(
    tenant_id: str,
    sku: str,
    region: str,
    service: Annotated[ForecastService, Depends(_service)],
    horizon_days: Annotated[int, Query(ge=1)] = 30,
) -> DemandDriversRead:
    return call(lambda: service.get_drivers(tenant_id, sku, region, horizon_days))
