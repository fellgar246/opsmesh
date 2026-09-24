"""Stockout risk used to rank replenishment and classify a delay.

The score is uncovered demand during the lead time, in thousandths of a unit.
Expected demand during the lead time is the selected forecast scaled by lead
time over the forecast horizon. Available units are subtracted. A higher score
means the SKU is closer to stocking out before replenishment can arrive. Zero
forecast demand ranks last because there is nothing to run out of.
"""

from opsmesh_api.workflows.models import RiskLevel


def stockout_risk_score(
    available_units: int,
    lead_time_days: int,
    selected_forecast: int,
    horizon_days: int,
) -> int:
    """Rank stockout risk from available units, lead time, and selected forecast."""

    if horizon_days <= 0:
        raise ValueError("forecast horizon must be positive")
    if lead_time_days < 0 or available_units < 0 or selected_forecast < 0:
        raise ValueError("stockout inputs must be non-negative")
    if selected_forecast == 0:
        return -available_units * 1000
    demand_milli = selected_forecast * lead_time_days * 1000 // horizon_days
    return demand_milli - available_units * 1000


def classify_stockout_risk(score: int) -> RiskLevel:
    """Positive uncovered demand is high. Exact cover is medium. A surplus is low."""

    if score > 0:
        return RiskLevel.HIGH
    if score == 0:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
