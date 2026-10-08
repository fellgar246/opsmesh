"""Forecast read shared by workflows and the demand specialist.

Domain code depends on this port. A provider name and a source record travel
with every reading so evidence can cite where the quantiles came from.
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opsmesh_api.services.errors import NotFoundError

PROVIDER_FIXTURE = "fixture"


class ForecastUnavailable(NotFoundError):
    """The forecast read failed closed. No other number may be substituted."""

    def __init__(self, message: str, *, provider_down: bool = False) -> None:
        self.message = message
        self.provider_down = provider_down
        super().__init__(message)


class ProviderConfig(BaseModel):
    """Where an external forecast provider lives, and which tenant ids it uses.

    Credentials stay on the adapter. This config does not carry them.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str
    tenant_mapping: dict[str, str]
    timeout_seconds: float = Field(gt=0)
    freshness_seconds: float | None = Field(default=None, ge=0)


class ForecastReading(BaseModel):
    """Domain forecast plus the provider and source record that produced it."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)
    horizon_days: int = Field(ge=1)
    p10: int = Field(ge=0)
    p50: int = Field(ge=0)
    p90: int = Field(ge=0)
    quality: float = Field(ge=0, le=1)
    observed_at: datetime
    provider: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be UTC")
        return value.astimezone(UTC)


class DemandForecastProvider(Protocol):
    """One forecast read. Implementations must not invent a missing quantile."""

    def read(self, tenant_id: str, sku: str, region: str, horizon_days: int) -> ForecastReading:
        """Return the reading for one tenant, SKU, region, and horizon."""
