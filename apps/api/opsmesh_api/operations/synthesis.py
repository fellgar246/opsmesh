"""Turn specialist facts into one feasible reorder outcome.

The quantity comes from the reorder rule sequence. This layer records which
constraints changed that quantity, keeps the specialist numbers as evidence,
and checks any English explanation against the outcome. It does not call a
language model and it does not average disagreeing recommendations.
"""

import re
from dataclasses import dataclass
from typing import Any

from opsmesh_api.operations.policy import (
    REASON_ADEQUATE_STOCK,
    REASON_INSUFFICIENT_BUDGET,
    REASON_MISSING_EVIDENCE,
    REASON_SUPPLIER_NOT_APPROVED,
    approval_for_total,
)
from opsmesh_api.operations.quantity import ReorderMath, case_pack_size, solve_reorder

_EVIDENCE_FIELDS = (
    "available_units",
    "selected_forecast",
    "minimum_order_quantity",
    "case_pack",
    "unit_cost_cents",
    "available_budget_cents",
    "supplier_status",
)

# Phrases that deny an approval the deterministic decision still requires.
_DENIAL_PHRASES = (
    "unnecessary",
    "not required",
    "does not require",
    "isn't required",
    "is not required",
    "no approval required",
    "approval is not needed",
    "not need approval",
)

_STATED_QUANTITY = re.compile(
    r"\b(?:purchase quantity|order quantity|quantity)\s+is\s+(\d+)\b"
    r"|\bquantity\s+of\s+(\d+)\b"
    r"|\b(?:buy|purchase|order)\s+(\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Recommendation:
    """A specialist number kept beside the feasible outcome."""

    agent: str
    field: str
    value: int
    evidence_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "field": self.field,
            "value": self.value,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class ConstraintOutcome:
    """Feasible reorder arguments, or a reason code, plus the binding constraints."""

    reason: str | None
    quantity: int
    math: ReorderMath | None
    binding_constraints: tuple[str, ...]
    requested_quantity: int | None
    recommendations: tuple[Recommendation, ...]
    requires_manager_approval: bool
    requires_finance_approval: bool
    evidence: dict[str, str]


def synthesize(
    *,
    service_level: float,
    p50: int | None,
    p90: int | None,
    available_units: int | None,
    case_pack: int | None,
    case_pack_supplied: bool,
    minimum_order_quantity: int | None,
    unit_cost_cents: int | None,
    supplier_status: str | None,
    available_cents: int | None,
    evidence: dict[str, str],
    recommendations: tuple[Recommendation, ...] = (),
) -> ConstraintOutcome:
    """Apply the reorder rules to cited facts. Missing numbers do not get guessed."""

    cited = _cited(evidence)
    numbers = (
        p50,
        p90,
        available_units,
        minimum_order_quantity,
        unit_cost_cents,
        available_cents,
    )
    if (
        not case_pack_supplied
        or cited is None
        or any(value is None for value in numbers)
        or not supplier_status
    ):
        return _missing(recommendations, cited or {})
    assert p50 is not None
    assert p90 is not None
    assert available_units is not None
    assert minimum_order_quantity is not None
    assert unit_cost_cents is not None
    assert available_cents is not None
    if (
        p50 < 0
        or p90 < 0
        or available_units < 0
        or minimum_order_quantity < 0
        or unit_cost_cents < 0
        or available_cents < 0
        or (case_pack is not None and case_pack < 0)
    ):
        return _missing(recommendations, cited)
    math = solve_reorder(
        service_level=service_level,
        p50=p50,
        p90=p90,
        available_units=available_units,
        case_pack=case_pack,
        minimum_order_quantity=minimum_order_quantity,
        unit_cost_cents=unit_cost_cents,
        supplier_status=supplier_status,
        available_cents=available_cents,
    )
    manager = False
    finance = False
    if math.reason is None and math.quantity > 0:
        manager, finance = approval_for_total(math.extended_cost_cents)
    kept = _recommendations(
        math=math,
        available_units=available_units,
        minimum_order_quantity=minimum_order_quantity,
        case_pack=case_pack,
        unit_cost_cents=unit_cost_cents,
        available_cents=available_cents,
        evidence=cited,
        extra=recommendations,
    )
    return ConstraintOutcome(
        reason=math.reason,
        quantity=math.quantity,
        math=math,
        binding_constraints=math.binding_constraints,
        requested_quantity=math.requested_quantity,
        recommendations=kept,
        requires_manager_approval=manager,
        requires_finance_approval=finance,
        evidence=cited,
    )


def synthesize_from_facts(
    *,
    service_level: float,
    inventory: dict[str, Any],
    demand: dict[str, Any],
    supplier: dict[str, Any],
    finance: dict[str, Any],
    evidence_refs: list[tuple[str, str]],
    recommendations: tuple[Recommendation, ...] = (),
) -> ConstraintOutcome:
    """Read one specialist fact set. Each required number must cite a source record."""

    by_source: dict[str, str] = {}
    for source, record_id in evidence_refs:
        by_source.setdefault(source, record_id)
    inventory_ref = by_source.get("inventory", "")
    demand_ref = by_source.get("demand", "")
    supplier_ref = by_source.get("supplier", "")
    finance_ref = by_source.get("finance", "")
    case_pack_supplied = "case_pack" in supplier
    case_pack = supplier.get("case_pack") if case_pack_supplied else None
    return synthesize(
        service_level=service_level,
        p50=_as_int(demand.get("p50")),
        p90=_as_int(demand.get("p90")),
        available_units=_as_int(inventory.get("available_units")),
        case_pack=_as_int(case_pack) if case_pack is not None else None,
        case_pack_supplied=case_pack_supplied,
        minimum_order_quantity=_as_int(supplier.get("minimum_order_quantity")),
        unit_cost_cents=_as_int(supplier.get("unit_cost_cents")),
        supplier_status=_as_text(supplier.get("status")),
        available_cents=_as_int(finance.get("available_cents")),
        evidence={
            "available_units": inventory_ref,
            "selected_forecast": demand_ref,
            "minimum_order_quantity": supplier_ref,
            "case_pack": supplier_ref,
            "unit_cost_cents": supplier_ref,
            "available_budget_cents": finance_ref,
            "supplier_status": supplier_ref,
        },
        recommendations=recommendations,
    )


def narrative_contradicts(
    explanation: str, *, requires_manager: bool, requires_finance: bool
) -> bool:
    """Return whether prose denies or invents an approval the policy facts require."""

    text = explanation.casefold()
    denies = any(phrase in text for phrase in _DENIAL_PHRASES)
    if requires_manager or requires_finance:
        return denies
    claims_manager = "manager approval" in text and not denies
    claims_finance = "finance approval" in text and not denies
    return claims_manager or claims_finance


def stated_purchase_quantities(text: str) -> list[int]:
    """Integers the text presents as a purchase quantity."""

    found: list[int] = []
    for match in _STATED_QUANTITY.finditer(text):
        raw = next(group for group in match.groups() if group is not None)
        found.append(int(raw))
    return found


def render_explanation(outcome: ConstraintOutcome) -> str:
    """English explanation that follows the outcome, including its reason code."""

    if outcome.reason == REASON_MISSING_EVIDENCE:
        return (
            "A required number was omitted, so no purchase quantity is proposed. "
            "Reason: missing_evidence."
        )
    if outcome.reason == REASON_INSUFFICIENT_BUDGET:
        return (
            "No purchase quantity is proposed. "
            "The affordable maximum is below the minimum order. "
            "Reason: insufficient_budget."
        )
    if outcome.reason == REASON_SUPPLIER_NOT_APPROVED:
        return (
            "No purchase quantity is proposed because the supplier is not approved. "
            "Reason: supplier_not_approved."
        )
    if outcome.reason == REASON_ADEQUATE_STOCK:
        return (
            "The purchase quantity is 0. Available stock covers the selected demand. "
            "Reason: adequate_stock."
        )
    binding = ""
    if outcome.binding_constraints:
        codes = ", ".join(outcome.binding_constraints)
        binding = f" Binding constraints: {codes}."
    return f"The purchase quantity is {outcome.quantity}.{binding} {_approval_sentence(outcome)}"


def explanation_is_consistent(text: str, outcome: ConstraintOutcome) -> bool:
    """A stated quantity and a stated approval must match the outcome."""

    if outcome.reason == REASON_MISSING_EVIDENCE:
        return "missing_evidence" in text and not _positive_quantities(text)
    if outcome.reason in {
        REASON_INSUFFICIENT_BUDGET,
        REASON_SUPPLIER_NOT_APPROVED,
        REASON_ADEQUATE_STOCK,
    }:
        if outcome.reason not in text or _positive_quantities(text):
            return False
        return not narrative_contradicts(text, requires_manager=False, requires_finance=False)
    if outcome.quantity < 0 or not re.search(rf"\b{outcome.quantity}\b", text):
        return False
    stated = stated_purchase_quantities(text)
    if any(item != outcome.quantity for item in stated):
        return False
    if not stated and not re.search(rf"\b{outcome.quantity}\b", text):
        return False
    return not narrative_contradicts(
        text,
        requires_manager=outcome.requires_manager_approval,
        requires_finance=outcome.requires_finance_approval,
    )


def accept_explanation(proposed: str | None, outcome: ConstraintOutcome) -> str:
    """Keep a matching explanation. Otherwise replace it with the outcome text."""

    if proposed and explanation_is_consistent(proposed.strip(), outcome):
        return proposed.strip()
    return render_explanation(outcome)


def _approval_sentence(outcome: ConstraintOutcome) -> str:
    if outcome.requires_manager_approval and outcome.requires_finance_approval:
        return "The purchase total requires manager and finance approval."
    if outcome.requires_manager_approval:
        return "The purchase total requires manager approval."
    if outcome.requires_finance_approval:
        return "The purchase total requires finance approval."
    return "The purchase total does not require additional approval."


def _positive_quantities(text: str) -> bool:
    return any(item > 0 for item in stated_purchase_quantities(text))


def _cited(evidence: dict[str, str]) -> dict[str, str] | None:
    cited: dict[str, str] = {}
    for field in _EVIDENCE_FIELDS:
        ref = evidence.get(field, "")
        if not isinstance(ref, str) or not ref.strip():
            return None
        cited[field] = ref
    return cited


def _missing(
    recommendations: tuple[Recommendation, ...],
    evidence: dict[str, str],
) -> ConstraintOutcome:
    return ConstraintOutcome(
        reason=REASON_MISSING_EVIDENCE,
        quantity=0,
        math=None,
        binding_constraints=(),
        requested_quantity=None,
        recommendations=recommendations,
        requires_manager_approval=False,
        requires_finance_approval=False,
        evidence=evidence,
    )


def _recommendations(
    *,
    math: ReorderMath,
    available_units: int,
    minimum_order_quantity: int,
    case_pack: int | None,
    unit_cost_cents: int,
    available_cents: int,
    evidence: dict[str, str],
    extra: tuple[Recommendation, ...],
) -> tuple[Recommendation, ...]:
    base = (
        Recommendation(
            "inventory",
            "available_units",
            available_units,
            evidence["available_units"],
        ),
        Recommendation(
            "demand",
            "selected_forecast",
            math.selected_demand,
            evidence["selected_forecast"],
        ),
        Recommendation(
            "supplier",
            "minimum_order_quantity",
            minimum_order_quantity,
            evidence["minimum_order_quantity"],
        ),
        Recommendation(
            "supplier",
            "case_pack",
            case_pack_size(case_pack),
            evidence["case_pack"],
        ),
        Recommendation(
            "supplier",
            "unit_cost_cents",
            unit_cost_cents,
            evidence["unit_cost_cents"],
        ),
        Recommendation(
            "finance",
            "available_budget_cents",
            available_cents,
            evidence["available_budget_cents"],
        ),
    )
    seen = {(item.agent, item.field, item.value) for item in base}
    kept = list(base)
    for item in extra:
        key = (item.agent, item.field, item.value)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return tuple(kept)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value
