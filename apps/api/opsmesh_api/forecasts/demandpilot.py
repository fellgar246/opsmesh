"""DemandPilot forecast adapter.

DemandPilot is a separate deployment. This module is the only place that knows
its document. A successful document looks like::

    GET {base_url}/v1/forecasts/{external_tenant}/{sku}/{region}?horizonDays=30
    Authorization: Bearer {token}

    {
      "forecastId": "fc-1001",
      "sku": "ABC-42",
      "region": "mx-central",
      "horizon": {"days": 30},
      "bands": {"p10": 280, "p50": 330, "p90": 400},
      "qualityScore": 0.86,
      "generatedAt": "2026-10-01T15:00:00Z"
    }

The token stays on this adapter. A missing P90, a document that does not match
the request, or a provider that does not respond fails the read. A cached
reading is returned only after a failed response, and only when a freshness
window was configured and the cached value for that same tenant is still inside it.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from urllib import error, request
from urllib.parse import quote, urlencode

from pydantic import ValidationError

from opsmesh_api.forecasts.port import ForecastReading, ForecastUnavailable, ProviderConfig

PROVIDER_DEMANDPILOT = "demandpilot"
_MAX_BODY_BYTES = 1_000_000

Clock = Callable[[], datetime]


class _CacheEntry:
    def __init__(self, tenant_id: str, reading: ForecastReading, stored_at: datetime) -> None:
        self.tenant_id = tenant_id
        self.reading = reading
        self.stored_at = stored_at


def map_demandpilot_document(
    document: dict[str, Any],
    *,
    tenant_id: str,
    sku: str,
    region: str,
    horizon_days: int,
) -> ForecastReading:
    """Map one DemandPilot document into a domain forecast reading."""

    if not isinstance(document, dict):
        raise ForecastUnavailable("The forecast provider returned an unexpected document.")
    _matches(document, "sku", sku)
    _matches(document, "region", region)
    horizon = document.get("horizon")
    if not isinstance(horizon, dict) or horizon.get("days") != horizon_days:
        raise ForecastUnavailable("The forecast horizon does not match the request.")
    bands = document.get("bands")
    if not isinstance(bands, dict) or "p90" not in bands or bands.get("p90") is None:
        raise ForecastUnavailable("The forecast is missing P90.")
    forecast_id = document.get("forecastId")
    if not isinstance(forecast_id, str) or not forecast_id.strip():
        raise ForecastUnavailable("The forecast source record is missing.")
    p10 = _whole(bands.get("p10"), "P10")
    p50 = _whole(bands.get("p50"), "P50")
    p90 = _whole(bands.get("p90"), "P90")
    if not p10 <= p50 <= p90:
        raise ForecastUnavailable("The forecast quantiles are not ordered.")
    try:
        return ForecastReading(
            tenant_id=tenant_id,
            sku=sku,
            region=region,
            horizon_days=horizon_days,
            p10=p10,
            p50=p50,
            p90=p90,
            quality=_quality(document.get("qualityScore")),
            observed_at=_observed(document.get("generatedAt")),
            provider=PROVIDER_DEMANDPILOT,
            source_record_id=forecast_id.strip(),
        )
    except (ForecastUnavailable, ValidationError, ValueError) as exc:
        if isinstance(exc, ForecastUnavailable):
            raise
        raise ForecastUnavailable("The forecast document is invalid.") from exc


class DemandPilotClient:
    """HTTP client for one DemandPilot deployment. Domain modules do not import this."""

    def __init__(self, config: ProviderConfig, api_token: str) -> None:
        self._base_url = config.base_url.rstrip("/")
        self._timeout = config.timeout_seconds
        self._api_token = api_token
        self._opener = request.build_opener(request.ProxyHandler({}))

    def fetch(
        self, external_tenant: str, sku: str, region: str, horizon_days: int
    ) -> dict[str, Any]:
        if not self._base_url:
            raise ForecastUnavailable(
                "The forecast provider is not configured.", provider_down=True
            )
        path = "/".join(
            quote(part, safe="") for part in ("v1", "forecasts", external_tenant, sku, region)
        )
        query = urlencode({"horizonDays": horizon_days})
        url = f"{self._base_url}/{path}?{query}"
        headers = {"Accept": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        call = request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(call, timeout=self._timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read(_MAX_BODY_BYTES + 1)
        except error.HTTPError as exc:
            raise ForecastUnavailable(
                "The forecast provider did not respond.", provider_down=True
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ForecastUnavailable(
                "The forecast provider did not respond.", provider_down=True
            ) from exc
        if status != 200 or len(raw) > _MAX_BODY_BYTES:
            raise ForecastUnavailable("The forecast provider did not respond.", provider_down=True)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ForecastUnavailable(
                "The forecast provider did not respond.", provider_down=True
            ) from exc
        if not isinstance(document, dict):
            raise ForecastUnavailable("The forecast provider returned an unexpected document.")
        return document


class DemandPilotProvider:
    """Read quantiles from DemandPilot and map them into a forecast reading."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        api_token: str = "",
        clock: Clock | None = None,
        client: DemandPilotClient | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = client if client is not None else DemandPilotClient(config, api_token)
        self._cache: dict[tuple[str, str, str, int], _CacheEntry] = {}
        self._lock = Lock()

    def read(self, tenant_id: str, sku: str, region: str, horizon_days: int) -> ForecastReading:
        external_tenant = self._config.tenant_mapping.get(tenant_id)
        if not external_tenant:
            raise ForecastUnavailable("The tenant is not mapped to a forecast provider tenant.")
        try:
            document = self._client.fetch(external_tenant, sku, region, horizon_days)
            reading = map_demandpilot_document(
                document,
                tenant_id=tenant_id,
                sku=sku,
                region=region,
                horizon_days=horizon_days,
            )
        except ForecastUnavailable as exc:
            if not exc.provider_down:
                raise
            cached = self._cached(tenant_id, sku, region, horizon_days)
            if cached is None:
                raise
            return cached
        self._store(tenant_id, sku, region, horizon_days, reading)
        return reading

    def _cached(
        self, tenant_id: str, sku: str, region: str, horizon_days: int
    ) -> ForecastReading | None:
        """Return a same-tenant reading only inside an explicit freshness window."""

        window = self._config.freshness_seconds
        if window is None:
            return None
        key = (tenant_id, sku, region, horizon_days)
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or entry.tenant_id != tenant_id:
                return None
            age = now - entry.stored_at
            if age > timedelta(seconds=window):
                del self._cache[key]
                return None
            return entry.reading

    def _store(
        self,
        tenant_id: str,
        sku: str,
        region: str,
        horizon_days: int,
        reading: ForecastReading,
    ) -> None:
        if self._config.freshness_seconds is None:
            return
        key = (tenant_id, sku, region, horizon_days)
        with self._lock:
            self._cache[key] = _CacheEntry(tenant_id, reading, self._clock())


def _matches(document: dict[str, Any], field: str, expected: str) -> None:
    value = document.get(field)
    if value is None:
        return
    if value != expected:
        raise ForecastUnavailable(f"The forecast {field} does not match the request.")


def _whole(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ForecastUnavailable(f"The forecast is missing {label}.")
    if isinstance(value, float) and not value.is_integer():
        raise ForecastUnavailable(f"The forecast {label} is not a whole number.")
    number = int(value)
    if number < 0:
        raise ForecastUnavailable(f"The forecast {label} is invalid.")
    return number


def _quality(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ForecastUnavailable("The forecast quality is missing.")
    quality = float(value)
    if quality < 0 or quality > 1:
        raise ForecastUnavailable("The forecast quality is invalid.")
    return quality


def _observed(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ForecastUnavailable("The forecast observation time is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForecastUnavailable("The forecast observation time is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastUnavailable("The forecast observation time must be UTC.")
    return parsed.astimezone(UTC)
