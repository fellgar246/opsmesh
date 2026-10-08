"""Choose the forecast provider. The external provider stays off unless selected."""

from sqlalchemy.orm import Session

from opsmesh_api.forecasts.demandpilot import DemandPilotProvider
from opsmesh_api.forecasts.fixture import FixtureForecastProvider
from opsmesh_api.forecasts.port import DemandForecastProvider, ProviderConfig
from opsmesh_api.settings import Settings


def select_forecast_provider(db: Session, settings: Settings) -> DemandForecastProvider:
    """Return the fixture provider unless DemandPilot was explicitly selected."""

    if settings.forecast_provider != "demandpilot":
        return FixtureForecastProvider(db)
    return DemandPilotProvider(
        ProviderConfig(
            base_url=settings.demandpilot_base_url,
            tenant_mapping=parse_tenant_map(settings.demandpilot_tenant_map),
            timeout_seconds=settings.demandpilot_timeout_seconds,
            freshness_seconds=_freshness(settings.demandpilot_freshness_seconds),
        ),
        api_token=settings.demandpilot_api_token,
    )


def parse_tenant_map(raw: str) -> dict[str, str]:
    """Parse ``local=external`` pairs. An empty string maps nothing."""

    mapping: dict[str, str] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        local, separator, external = item.partition("=")
        local = local.strip()
        external = external.strip()
        if not separator or not local or not external:
            raise ValueError("tenant mapping entries must be local=external")
        mapping[local] = external
    return mapping


def _freshness(raw: str) -> float | None:
    """A blank window disables cache fallback. A number is the window in seconds."""

    text = raw.strip()
    if not text:
        return None
    return float(text)
