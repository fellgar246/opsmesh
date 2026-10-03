"""Constraint synthesis keeps the feasible quantity and checks the explanation."""

import inspect

from opsmesh_api.operations import synthesis
from opsmesh_api.operations.policy import REASON_INSUFFICIENT_BUDGET, REASON_MISSING_EVIDENCE
from opsmesh_api.operations.synthesis import (
    Recommendation,
    accept_explanation,
    synthesize,
)

_EVIDENCE = {
    "available_units": "inventory:sku",
    "selected_forecast": "demand:sku",
    "minimum_order_quantity": "supplier:sku",
    "case_pack": "supplier:sku",
    "unit_cost_cents": "supplier:sku",
    "available_budget_cents": "finance:tenant",
    "supplier_status": "supplier:sku",
}


def _conflict(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "service_level": 0.90,
        "p50": 350,
        "p90": 350,
        "available_units": 0,
        "case_pack": 50,
        "case_pack_supplied": True,
        "minimum_order_quantity": 100,
        "unit_cost_cents": 100,
        "supplier_status": "approved",
        "available_cents": 25_000,
        "evidence": dict(_EVIDENCE),
    }
    payload.update(overrides)
    return payload


def test_budget_cap_yields_a_packed_quantity_without_a_model() -> None:
    source = inspect.getsource(synthesis)
    assert "ModelPort" not in source
    assert ".complete(" not in source
    outcome = synthesize(**_conflict())  # type: ignore[arg-type]
    assert outcome.quantity == 250
    assert outcome.quantity % 50 == 0
    assert outcome.quantity >= 100
    assert outcome.reason is None
    assert "budget" in outcome.binding_constraints
    assert outcome.requested_quantity == 350
    assert any(
        item.agent == "demand" and item.field == "selected_forecast" and item.value == 350
        for item in outcome.recommendations
    )
    assert outcome.requires_manager_approval is False
    assert outcome.requires_finance_approval is False


def test_budget_below_the_minimum_order_cites_insufficient_budget() -> None:
    outcome = synthesize(**_conflict(available_cents=4_000))  # type: ignore[arg-type]
    assert outcome.reason == REASON_INSUFFICIENT_BUDGET
    assert outcome.quantity == 0
    text = accept_explanation("Buy 40 units anyway.", outcome)
    assert "insufficient_budget" in text
    assert "40" not in text


def test_unapproved_supplier_proposes_no_positive_quantity() -> None:
    outcome = synthesize(**_conflict(supplier_status="blocked"))  # type: ignore[arg-type]
    assert outcome.quantity == 0
    assert outcome.quantity <= 0
    assert outcome.reason == "supplier_not_approved"


def test_omitted_forecast_fails_with_missing_evidence() -> None:
    outcome = synthesize(**_conflict(p90=None))  # type: ignore[arg-type]
    assert outcome.reason == REASON_MISSING_EVIDENCE
    assert outcome.math is None
    assert outcome.quantity == 0


def test_a_contradictory_quantity_is_replaced() -> None:
    outcome = synthesize(
        service_level=0.90,
        p50=310,
        p90=345,
        available_units=128,
        case_pack=50,
        case_pack_supplied=True,
        minimum_order_quantity=100,
        unit_cost_cents=3700,
        supplier_status="approved",
        available_cents=4_200_000,
        evidence=_EVIDENCE,
        recommendations=(Recommendation("demand", "suggested_quantity", 400, "demand:sku"),),
    )
    assert outcome.quantity == 250
    assert outcome.requires_manager_approval is True
    assert outcome.requires_finance_approval is False
    assert any(item.value == 400 for item in outcome.recommendations)
    rejected = "The purchase quantity is 300. Finance approval is required."
    accepted = accept_explanation(rejected, outcome)
    assert accepted != rejected
    assert "300" not in accepted
    assert "250" in accepted
    assert "requires manager approval" in accepted
    assert "finance approval" not in accepted.casefold()
    matching = "The purchase quantity is 250. The purchase total requires manager approval."
    assert accept_explanation(matching, outcome) == matching


def test_missing_evidence_reference_does_not_invent_a_quantity() -> None:
    evidence = dict(_EVIDENCE)
    evidence["available_budget_cents"] = ""
    outcome = synthesize(**_conflict(evidence=evidence))  # type: ignore[arg-type]
    assert outcome.reason == REASON_MISSING_EVIDENCE
    assert outcome.quantity == 0
