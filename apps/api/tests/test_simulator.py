"""Local enterprise simulator reads, isolation, and fixture loading."""

import json
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from opsmesh_api.db import Base
from opsmesh_api.main import create_app
from opsmesh_api.services.errors import FixtureError
from opsmesh_api.services.finance import estimate_purchase_impact
from opsmesh_api.services.models import SkuStock
from opsmesh_api.services.schemas import ForecastRead
from opsmesh_api.services.seed import FIXTURE_PATH, load_fixtures, parse_fixtures
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
REGION = "mx-central"
MANAGER_APPROVAL_CENTS = 500_000
FINANCE_APPROVAL_CENTS = 2_500_000


def _round_up(quantity: int, case_pack: int) -> int:
    return ((quantity + case_pack - 1) // case_pack) * case_pack


def _reorder_quantity(available: int, selected_demand: int, case_pack: int, moq: int) -> int:
    """Reorder math uses only fields returned by the read APIs."""

    gross_need = selected_demand - available
    if gross_need <= 0:
        return 0
    quantity = _round_up(gross_need, case_pack)
    if quantity < moq:
        quantity = _round_up(moq, case_pack)
    return quantity


def _utc(value: str) -> None:
    observed = value.replace("Z", "+00:00")
    from datetime import datetime

    parsed = datetime.fromisoformat(observed)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_seed_reads_match_the_reference_sku(client: TestClient) -> None:
    snapshot = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["on_hand_units"] == 140
    assert body["reserved_units"] == 12
    assert body["available_units"] == 128
    assert body["available_units"] == body["on_hand_units"] - body["reserved_units"]
    assert body["reorder_point_units"] == 180
    _utc(body["observed_at"])
    ForecastRead.model_validate(client.get(f"/tenants/{NORTHWIND}/forecasts/{SKU}/{REGION}").json())

    forecast = client.get(f"/tenants/{NORTHWIND}/forecasts/{SKU}/{REGION}")
    assert forecast.status_code == 200
    forecast_body = forecast.json()
    assert forecast_body["horizon_days"] == 30
    assert forecast_body["p10"] == 275
    assert forecast_body["p50"] == 310
    assert forecast_body["p90"] == 345
    assert forecast_body["trend"] == "rising"
    assert forecast_body["quality"] == 0.75
    assert forecast_body["seasonality"]["label"] == "in-season"
    assert forecast_body["promotion"]["active"] is False
    _utc(forecast_body["observed_at"])

    preferred = client.get(
        f"/tenants/{NORTHWIND}/suppliers/preferred",
        params={"sku": SKU},
    )
    assert preferred.status_code == 200
    supplier_id = preferred.json()["supplier_id"]
    assert supplier_id == "andina-supply"

    terms = client.get(
        f"/tenants/{NORTHWIND}/suppliers/{supplier_id}/terms",
        params={"sku": SKU},
    )
    assert terms.status_code == 200
    terms_body = terms.json()
    assert terms_body["lead_time_days"] == 12
    assert terms_body["minimum_order_quantity"] == 100
    assert terms_body["case_pack"] == 50
    assert terms_body["unit_cost_cents"] == 3700
    assert terms_body["status"] == "approved"
    assert terms_body["reliability_score"] == 0.875

    budget = client.get(f"/tenants/{NORTHWIND}/finance/budget")
    assert budget.status_code == 200
    assert budget.json()["available_cents"] == 4_200_000
    assert budget.json()["committed_cents"] == 180_000

    committed = client.get(f"/tenants/{NORTHWIND}/finance/committed-spend")
    assert committed.json()["committed_cents"] == 180_000

    parameters = client.get(
        f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}/reorder-parameters"
    )
    assert parameters.status_code == 200
    assert parameters.json()["safety_stock_units"] == 40

    stockouts = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}/stockouts")
    assert stockouts.status_code == 200
    assert stockouts.json()["stockouts"][0]["units_short"] == 22
    _utc(stockouts.json()["stockouts"][0]["started_at"])


def test_reorder_can_be_computed_from_api_data_alone(client: TestClient) -> None:
    snapshot = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}").json()
    forecast = client.get(f"/tenants/{NORTHWIND}/forecasts/{SKU}/{REGION}").json()
    supplier_id = client.get(
        f"/tenants/{NORTHWIND}/suppliers/preferred",
        params={"sku": SKU},
    ).json()["supplier_id"]
    terms = client.get(
        f"/tenants/{NORTHWIND}/suppliers/{supplier_id}/terms",
        params={"sku": SKU},
    ).json()
    budget = client.get(f"/tenants/{NORTHWIND}/finance/budget").json()

    # Service level 0.90 selects the P90 quantile.
    quantity = _reorder_quantity(
        available=snapshot["available_units"],
        selected_demand=forecast["p90"],
        case_pack=terms["case_pack"],
        moq=terms["minimum_order_quantity"],
    )
    impact = client.get(
        f"/tenants/{NORTHWIND}/finance/purchase-impact",
        params={"quantity": quantity, "unit_cost_cents": terms["unit_cost_cents"]},
    )
    assert impact.status_code == 200
    extended = impact.json()["extended_cost_cents"]
    assert quantity == 250
    assert extended == 925_000
    assert extended == estimate_purchase_impact(quantity, terms["unit_cost_cents"])
    assert extended <= budget["available_cents"]
    # A purchase total above 5000 currency units requires manager approval.
    assert extended > MANAGER_APPROVAL_CENTS
    assert extended <= FINANCE_APPROVAL_CENTS


def test_cross_tenant_read_is_not_found(client: TestClient) -> None:
    response = client.get(f"/tenants/{CONTOSO}/inventory/skus/{SKU}/regions/{REGION}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    leaked = ("128", "310", "345", "3700", "4200000", "andina-supply")
    for token in leaked:
        assert token not in response.text

    order = client.get(f"/tenants/{CONTOSO}/procurement/purchase-orders/po-northwind-1001")
    assert order.status_code == 404
    assert "3700" not in order.text

    own = client.get(f"/tenants/{CONTOSO}/finance/budget")
    assert own.status_code == 200
    assert own.json()["available_cents"] == 80_000
    assert "4200000" not in own.text


def test_purchase_orders_are_listed_and_nothing_submits_them(client: TestClient) -> None:
    listed = client.get(f"/tenants/{NORTHWIND}/procurement/purchase-orders")
    assert listed.status_code == 200
    orders = listed.json()["purchase_orders"]
    assert len(orders) == 3
    open_order = next(
        order for order in orders if order["purchase_order_id"] == "po-northwind-1001"
    )
    assert open_order["supplier_id"] == "andina-supply"
    assert open_order["sku"] == SKU
    assert open_order["quantity"] == 200
    assert open_order["status"] == "open"
    assert open_order["expected_receipt_on"] == "2026-09-28"

    fetched = client.get(f"/tenants/{NORTHWIND}/procurement/purchase-orders/po-northwind-1001")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "open"

    supplier_open = client.get(f"/tenants/{NORTHWIND}/suppliers/andina-supply/purchase-orders")
    assert supplier_open.status_code == 200
    supplier_ids = [order["purchase_order_id"] for order in supplier_open.json()["purchase_orders"]]
    assert supplier_ids == ["po-northwind-1001"]

    app = create_app()
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    paths: list[str] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert route.methods is not None
            paths.append(route.path)
            if "/procurement/" in route.path or route.path.endswith("/purchase-orders"):
                assert not (set(route.methods) & write_methods)
    blob = " ".join(paths).lower()
    for token in ("submit", "dispatch", "send-order", "place-order"):
        assert token not in blob


def test_unapproved_supplier_and_active_delay(client: TestClient) -> None:
    terms = client.get(
        f"/tenants/{NORTHWIND}/suppliers/unapproved-mills/terms",
        params={"sku": "XYZ-9"},
    )
    assert terms.status_code == 200
    assert terms.json()["status"] == "unapproved"

    delay = client.get(f"/tenants/{NORTHWIND}/suppliers/pacific-freight/delay")
    assert delay.status_code == 200
    assert delay.json()["active"] is True
    assert delay.json()["delay_days"] == 6
    _utc(delay.json()["started_at"])

    missing_delay = client.get(f"/tenants/{NORTHWIND}/suppliers/andina-supply/delay")
    assert missing_delay.status_code == 404


def test_unknown_records_are_not_found(client: TestClient) -> None:
    assert (
        client.get(f"/tenants/{NORTHWIND}/inventory/skus/NO-SUCH/regions/{REGION}").status_code
        == 404
    )
    assert (
        client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/mx-north").status_code == 404
    )
    assert (
        client.get(
            f"/tenants/{NORTHWIND}/suppliers/missing-supplier/terms",
            params={"sku": SKU},
        ).status_code
        == 404
    )
    assert client.get("/tenants/unknown/finance/budget").status_code == 404
    missing_horizon = client.get(
        f"/tenants/{NORTHWIND}/forecasts/{SKU}/{REGION}",
        params={"horizon_days": 7},
    )
    assert missing_horizon.status_code == 404


def test_reserved_above_on_hand_conflicts_without_clamping(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
        assert stock is not None
        stock.on_hand = 10
        stock.reserved = 15
        stock.available = -5
        db.commit()

    response = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["on_hand"] == 10
    assert detail["reserved"] == 15
    assert "available" not in detail
    assert "available_units" not in response.text

    with session_factory() as db:
        stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
        assert stock is not None
        assert stock.available == -5
        assert stock.on_hand == 10
        assert stock.reserved == 15


def test_fractional_currency_is_rejected_and_leaves_rows(
    client: TestClient,
) -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["tenants"][0]["budget"]["available_cents"] = 4200000.5
    with pytest.raises(FixtureError, match="integer cents"):
        parse_fixtures(raw)
    budget = client.get(f"/tenants/{NORTHWIND}/finance/budget")
    assert budget.json()["available_cents"] == 4_200_000


def test_clamped_and_negative_available_are_rejected() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    stock = next(item for item in raw["tenants"][0]["stocks"] if item["sku"] == SKU)
    stock["on_hand"] = 4
    stock["reserved"] = 9
    stock["available"] = 0
    with pytest.raises(FixtureError, match="on-hand minus reserved"):
        parse_fixtures(raw)

    stock["available"] = -5
    with pytest.raises(FixtureError, match="must not be negative"):
        parse_fixtures(raw)


def test_forecast_quality_bounds() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    forecast = next(item for item in raw["tenants"][0]["forecasts"] if item["sku"] == SKU)
    forecast["quality"] = 0
    parse_fixtures(raw)
    forecast["quality"] = 1
    parse_fixtures(raw)
    forecast["quality"] = 1.01
    with pytest.raises(FixtureError, match="0 to 1"):
        parse_fixtures(raw)
    with pytest.raises(ValidationError):
        ForecastRead(
            tenant_id=NORTHWIND,
            sku=SKU,
            region=REGION,
            horizon_days=30,
            p10=1,
            p50=2,
            p90=3,
            trend="rising",
            quality=1.1,
            seasonality={"label": "in-season", "index": 1.25},
            promotion={"active": False, "name": None},
            observed_at="2026-09-01T00:00:00Z",
        )


def test_reload_is_idempotent(client: TestClient, session_factory: sessionmaker[Session]) -> None:
    first = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}").json()
    with session_factory() as db:
        stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
        assert stock is not None
        stock.on_hand = 13
        stock.reserved = 12
        stock.available = 1
        db.commit()
        load_fixtures(db)
    second = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}").json()
    assert second == first
    with session_factory() as db:
        count = db.scalar(select(func.count()).select_from(SkuStock))
    assert count == 4

    bind = session_factory.kw["bind"]
    Base.metadata.drop_all(bind)
    Base.metadata.create_all(bind)
    with session_factory() as db:
        load_fixtures(db)
    third = client.get(f"/tenants/{NORTHWIND}/inventory/skus/{SKU}/regions/{REGION}").json()
    assert third == first


def test_purchase_impact_does_not_persist(client: TestClient) -> None:
    before = client.get(f"/tenants/{NORTHWIND}/procurement/purchase-orders").json()
    impact = client.get(
        f"/tenants/{NORTHWIND}/finance/purchase-impact",
        params={"quantity": 250, "unit_cost_cents": 3700},
    )
    assert impact.status_code == 200
    assert impact.json()["extended_cost_cents"] == 925_000
    after = client.get(f"/tenants/{NORTHWIND}/procurement/purchase-orders").json()
    assert after == before
    assert estimate_purchase_impact(250, 3700) == 925_000
