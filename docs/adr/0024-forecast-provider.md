# ADR-024: Forecast reads sit behind a provider port

## Status

Accepted

## Context

The reorder uses P50 or P90 from a demand forecast. Those quantiles are seeded in the local simulator, and the golden proposal depends on them. A separately deployed forecasting product can supply the same quantiles, but the reorder rules, the proposal, and the workflow records should not learn that product's document or its client.

## Decision

Workflows and the demand specialist read forecasts through `DemandForecastProvider`. The default implementation is `FixtureForecastProvider`, which returns the seeded quantiles. `FORECAST_PROVIDER=demandpilot` selects `DemandPilotProvider`. That adapter calls DemandPilot over HTTP, maps the document into a forecast reading, and is the only module that knows the document. Tenant ids are translated through an explicit map and are not sent unchanged. Evidence records the provider name and the source record id.

A missing P90 or a provider that does not respond fails the read. The workflow ends `missing_evidence` and does not substitute the fixture. A previous reading is reused only when `DEMANDPILOT_FRESHNESS_SECONDS` is set and the cached value for that same tenant is still inside the window. The external provider and the freshness window are off by default. The API token stays on the adapter.

## Consequences

- The golden fixture reorder is unchanged when the external provider is off.
- A different P90 from DemandPilot changes the proposal only through the existing reorder rules.
- Workflow and specialist modules do not import the DemandPilot client.
