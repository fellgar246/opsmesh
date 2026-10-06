"""Public list prices for estimated model cost.

The table is configuration. Updating it does not change workflow code.
A model that ran without a listed price is unknown, not zero.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

_TABLE_PATH = Path(__file__).with_name("prices.json")


@lru_cache
def _table() -> dict[str, tuple[Decimal, Decimal]]:
    raw = json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    prices: dict[str, tuple[Decimal, Decimal]] = {}
    for model_id, row in raw.items():
        prices[model_id] = (
            Decimal(row["input_usd_per_million"]),
            Decimal(row["output_usd_per_million"]),
        )
    return prices


def price_known(model_id: str) -> bool:
    """Return whether the price table lists this model."""

    return model_id in _table()


def estimate_cost_cents(
    *,
    model_calls: int,
    input_tokens: int,
    output_tokens: int,
    model_id: str | None,
    unpriced: bool,
) -> int | None:
    """Return cents, zero when no model ran, or None when a price is missing."""

    if model_calls <= 0:
        return 0
    if unpriced or model_id is None or model_id not in _table():
        return None
    input_rate, output_rate = _table()[model_id]
    usd = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / Decimal(
        1_000_000
    )
    return int((usd * 100).to_integral_value(rounding=ROUND_HALF_UP))


def usage_tokens(source: object | None) -> tuple[int, int, bool, str | None]:
    """Read token totals from a client or planner. Missing attributes are zero."""

    if source is None:
        return 0, 0, False, None
    model_id = getattr(source, "priced_model_id", None)
    if not isinstance(model_id, str) or not model_id:
        model_id = None
    return (
        _whole(getattr(source, "input_tokens", 0)),
        _whole(getattr(source, "output_tokens", 0)),
        bool(getattr(source, "unpriced_model", False)),
        model_id,
    )


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
