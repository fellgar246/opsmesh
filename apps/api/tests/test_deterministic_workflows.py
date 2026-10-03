"""Deterministic reorder, delay impact, and budget plan workflows."""

from fastapi.testclient import TestClient
from opsmesh_api.operations.policy import approval_for_total
from opsmesh_api.operations.quantity import rounded_need, solve_reorder
from opsmesh_api.operations.risk import stockout_risk_score
from opsmesh_api.services.models import Budget, Forecast, SkuStock
from opsmesh_api.settings import Settings
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
REGION = "mx-central"
REQUESTED_BY = "analyst@northwind.example"


def _orders(client: TestClient, tenant_id: str = NORTHWIND) -> list[dict[str, object]]:
    response = client.get(f"/tenants/{tenant_id}/procurement/purchase-orders")
    assert response.status_code == 200
    body: list[dict[str, object]] = response.json()["purchase_orders"]
    return body


def _reorder(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "requested_by": REQUESTED_BY,
        "sku": SKU,
        "region": REGION,
        "service_level": 0.90,
    }
    payload.update(overrides)
    response = client.post(f"/tenants/{NORTHWIND}/operations/reorder", json=payload)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_flags_stay_off_and_model_calls_are_zero(client: TestClient) -> None:
    settings = Settings(_env_file=None)
    assert settings.model_provider_enabled is False
    assert settings.cloud_integrations_enabled is False
    assert settings.aws_agentic_features_enabled is False
    before = _orders(client)
    body = _reorder(client)
    assert body["selected_demand"] == 345
    assert body["gross_need"] == 217
    assert body["quantity"] == 250
    assert body["extended_cost_cents"] == 925_000
    assert body["requires_manager_approval"] is True
    assert body["requires_finance_approval"] is False
    assert body["status"] == "WAITING_APPROVAL"
    summary = body["cost_summary"]
    assert isinstance(summary, dict)
    assert summary["model_calls"] == 0
    assert summary["estimated_cost_cents"] == 0
    assert summary["tool_calls"] > 0
    assert _orders(client) == before

    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    assert run.status_code == 200
    stored = run.json()
    assert stored["workflow_type"] == "reorder"
    assert stored["requested_by"] == REQUESTED_BY
    assert stored["cost_summary"]["model_calls"] == 0
    assert {task["agent_type"] for task in stored["tasks"]} == {
        "inventory",
        "demand",
        "supplier",
        "finance",
        "policy",
    }
    assert all(len(task["results"]) == 1 for task in stored["tasks"])
    assert all(task["results"][0]["model_id"] is None for task in stored["tasks"])
    record_ids = {item["source_record_id"] for item in stored["evidence"]}
    assert "ABC-42:mx-central" in record_ids
    assert "andina-supply:ABC-42" in record_ids
    assert stored["evidence"]
    assert len(stored["proposals"]) == 1
    proposal = stored["proposals"][0]
    assert proposal["arguments"]["quantity"] == 250
    assert proposal["arguments"]["extended_cost_cents"] == 925_000
    assert proposal["arguments"]["requires_manager_approval"] is True
    assert "case_pack" in proposal["arguments"]["binding_constraints"]
    assert "250" in proposal["arguments"]["explanation"]
    assert "requires manager approval" in proposal["arguments"]["explanation"]
    roles = {approval["required_role"] for approval in proposal["approvals"]}
    assert roles == {"procurement_manager"}


def test_repeated_runs_share_proposal_arguments(client: TestClient) -> None:
    first = _reorder(client)
    second = _reorder(client)
    assert first["run_id"] != second["run_id"]
    assert first["proposal_arguments"] == second["proposal_arguments"]
    assert "run_id" not in first["proposal_arguments"]  # type: ignore[operator]


def test_adequate_stock_needs_no_approval(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        stock = db.get(SkuStock, (NORTHWIND, SKU, REGION))
        assert stock is not None
        stock.on_hand = 400
        stock.reserved = 0
        stock.available = 400
        db.commit()
    body = _reorder(client)
    assert body["quantity"] == 0
    assert body["reason"] == "adequate_stock"
    assert body["requires_manager_approval"] is False
    assert body["requires_finance_approval"] is False
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    proposal = run.json()["proposals"][0]
    assert proposal["arguments"]["quantity"] == 0
    assert proposal["approvals"] == []


def test_insufficient_budget_drafts_no_purchase(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        budget = db.get(Budget, NORTHWIND)
        assert budget is not None
        budget.available_cents = 1_000
        db.commit()
    before = _orders(client)
    body = _reorder(client)
    assert body["reason"] == "insufficient_budget"
    assert body["proposal_arguments"] is None
    assert "insufficient_budget" in str(body["explanation"])
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    assert run.json()["proposals"] == []
    assert _orders(client) == before


def test_unapproved_supplier_is_blocked(client: TestClient) -> None:
    before = _orders(client)
    body = _reorder(client, sku="XYZ-9")
    assert body["reason"] == "supplier_not_approved"
    assert body["quantity"] == 0
    assert body["proposal_arguments"] is None
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    assert run.json()["proposals"] == []
    assert _orders(client) == before


def test_missing_forecast_does_not_invent_numbers(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as db:
        forecast = db.get(Forecast, (NORTHWIND, SKU, REGION, 30))
        assert forecast is not None
        db.delete(forecast)
        db.commit()
    body = _reorder(client)
    assert body["status"] == "FAILED"
    assert body["reason"] == "missing_evidence"
    assert body["selected_demand"] is None
    assert body["quantity"] is None
    assert "345" not in str(body)
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    assert run.json()["proposals"] == []
    assert run.json()["cost_summary"]["model_calls"] == 0


def test_service_level_below_threshold_selects_p50(client: TestClient) -> None:
    body = _reorder(client, service_level=0.89)
    assert body["selected_quantile"] == "p50"
    assert body["selected_demand"] == 310
    assert body["gross_need"] == 182
    assert body["quantity"] == 200


def test_supplier_delay_lists_orders_and_does_not_buy(client: TestClient) -> None:
    settings = Settings(_env_file=None)
    assert settings.model_provider_enabled is False
    before = _orders(client)
    response = client.post(
        f"/tenants/{NORTHWIND}/operations/supplier-delays",
        json={"requested_by": REQUESTED_BY, "supplier_id": "pacific-freight"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["affected_orders"] == ["po-northwind-2002"]
    assert body["affected_skus"] == ["DEF-7"]
    assert body["stockout_risk"][0]["sku"] == "DEF-7"
    assert body["stockout_risk"][0]["level"] in {"low", "medium", "high"}
    codes = {item["code"] for item in body["mitigations"]}
    assert "do_not_place_order" in codes
    assert "expedite_open_order" in codes
    assert body["cost_summary"]["model_calls"] == 0
    assert _orders(client) == before
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    stored = run.json()
    assert stored["workflow_type"] == "supplier_delay"
    assert len(stored["proposals"]) == 1
    assert "po-northwind-2002" in {item["source_record_id"] for item in stored["evidence"]}


def test_delay_in_another_tenant_is_not_found(client: TestClient) -> None:
    response = client.post(
        f"/tenants/{CONTOSO}/operations/supplier-delays",
        json={"requested_by": REQUESTED_BY, "supplier_id": "pacific-freight"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "port congestion" not in response.text
    assert "pacific-freight" not in response.text


def test_budget_plan_funds_higher_risk_first(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    settings = Settings(_env_file=None)
    assert settings.cloud_integrations_enabled is False
    with session_factory() as db:
        budget = db.get(Budget, NORTHWIND)
        assert budget is not None
        budget.available_cents = 925_000
        db.commit()
    before = _orders(client)
    response = client.post(
        f"/tenants/{NORTHWIND}/operations/budget-plans",
        json={
            "requested_by": REQUESTED_BY,
            "candidates": [
                {"sku": "DEF-7", "region": REGION, "service_level": 0.90},
                {"sku": SKU, "region": REGION, "service_level": 0.90},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    lines = body["lines"]
    assert lines[0]["sku"] == SKU
    assert lines[0]["funded"] is True
    assert lines[0]["quantity"] == 250
    assert lines[0]["extended_cost_cents"] == 925_000
    assert lines[0]["requires_manager_approval"] is True
    assert lines[0]["requires_finance_approval"] is False
    assert lines[1]["sku"] == "DEF-7"
    assert lines[1]["funded"] is False
    assert lines[1]["reason"] == "insufficient_budget"
    assert body["cost_summary"]["model_calls"] == 0
    assert _orders(client) == before
    run = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    stored = run.json()
    assert stored["workflow_type"] == "budget_plan"
    assert len(stored["proposals"]) == 1
    assert stored["proposals"][0]["arguments"]["lines"][0]["requires_manager_approval"] is True


def test_quantity_rules_round_minimum_order_up_to_the_case_pack() -> None:
    gross_need, quantity, _binding = rounded_need(0, 10, None, 1)
    assert gross_need == 10
    assert quantity == 10
    _, raised, _raised_binding = rounded_need(0, 10, 30, 50)
    assert raised == 60
    decision = solve_reorder(
        service_level=0.5,
        p50=10,
        p90=99,
        available_units=0,
        case_pack=30,
        minimum_order_quantity=50,
        unit_cost_cents=100,
        supplier_status="approved",
        available_cents=10_000,
    )
    assert decision.selected_quantile == "p50"
    assert decision.quantity == 60
    assert approval_for_total(500_000) == (False, False)
    assert approval_for_total(500_001) == (True, False)
    assert approval_for_total(2_500_001) == (True, True)
    abc = stockout_risk_score(128, 12, 345, 30)
    other = stockout_risk_score(45, 9, 55, 30)
    assert abc > other
