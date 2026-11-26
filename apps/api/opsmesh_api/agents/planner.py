"""Turn an operational request into a validated specialist plan.

The planner chooses tasks. It does not run them and it does not read
simulator databases.
"""

import json
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from opsmesh_api.agents.ceilings import (
    INPUT_TOKEN_CEILING,
    MAX_SUPERVISOR_ITERATIONS,
    OUTPUT_TOKEN_CEILING,
    estimate_tokens,
    fit_to_input_ceiling,
)
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.agents.model_port import (
    ModelCompletionRequest,
    ModelPort,
    ModelProviderError,
)
from opsmesh_api.agents.plan_schema import (
    SPECIALIST_ALLOWLIST,
    PlanValidationError,
    SupervisorPlan,
    extract_json_object,
    validate_plan,
)

SUPERVISOR_PROMPT_VERSION = "supervisor-planner-1"
_REORDER_REASON = "Check inventory, demand, and supplier facts, then finance, then policy."
_DELAY_REASON = (
    "Review the supplier delay against demand and inventory. This plan does not purchase."
)
_DELAY_PURCHASE_REASON = (
    "Review the supplier delay and include finance because the request asks for a purchase."
)
_BUDGET_REASON = "Rank candidate purchases against the available budget, then apply policy."
_INCIDENT_REASON = "Investigate the operational anomaly with read-only specialist tasks."
_PURCHASE_MARKERS = ("purchase", "buy", "procure")
_SYSTEM_PROMPT = (
    "You are the supervisor. Return one JSON object with tasks, dependencies, and reason. "
    "Each task has id, agent, input, and read_only true. "
    "Agents must be inventory, demand, supplier, finance, or policy. "
    "Do not call tools, query databases, or add agent types. "
    "Each dependency has task_id and depends_on. The reason is one English sentence."
)


class WorkflowKind(StrEnum):
    REORDER = "reorder"
    SUPPLIER_DELAY = "supplier_delay"
    BUDGET_PLAN = "budget_plan"
    INCIDENT_ANALYSIS = "incident_analysis"


class PlanCandidate(BaseModel):
    """A candidate named on the request. The planner does not look it up."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)


class PlanningRequest(BaseModel):
    """The operational request the supervisor is allowed to see."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: WorkflowKind
    text: str = ""
    sku: str | None = None
    region: str | None = None
    supplier_id: str | None = None
    horizon_days: int = Field(default=30, ge=1)
    candidates: list[PlanCandidate] = Field(default_factory=list)
    asks_for_purchase: bool = False


class IterationBudget:
    """Counts supervisor passes on one workflow. The fifth pass fails."""

    def __init__(self) -> None:
        self.count = 0

    def tick(self) -> int:
        if self.count >= MAX_SUPERVISOR_ITERATIONS:
            raise PlanValidationError(
                "supervisor_iteration_limit",
                "The supervisor exceeded 4 iterations on this workflow.",
                iterations=self.count,
            )
        self.count += 1
        return self.count


class SupervisorPlanner(Protocol):
    """Port for one planning pass. Implementations do not receive a database."""

    iterations: int
    model_calls: int

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        """Return a validated plan or raise PlanValidationError."""


class DeterministicPlanner:
    """Fallback plans for local runs. Tests do not need a model."""

    def __init__(self) -> None:
        self.iterations = 0
        self.model_calls = 0

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        self.iterations = IterationBudget().tick()
        self.model_calls = 0
        if request.workflow_type == WorkflowKind.REORDER:
            document = _reorder_document(request)
        elif request.workflow_type == WorkflowKind.SUPPLIER_DELAY:
            document = _delay_document(request)
        elif request.workflow_type == WorkflowKind.BUDGET_PLAN:
            document = _budget_document(request)
        else:
            document = _incident_document(request)
        return validate_plan(document)


class ModelSupervisorPlanner:
    """Ask the model port for a plan, then run the same validator."""

    def __init__(self, model: ModelPort, model_id: str) -> None:
        self._model = model
        self._model_id = model_id
        self.iterations = 0
        self.model_calls = 0

    def plan(self, request: PlanningRequest) -> SupervisorPlan:
        self.iterations = 0
        self.model_calls = 0
        budget = IterationBudget()
        feedback = ""
        last_code = "invalid_plan"
        last_message = "The model did not return a valid plan."
        while True:
            try:
                self.iterations = budget.tick()
            except PlanValidationError:
                raise PlanValidationError(
                    "supervisor_iteration_limit",
                    f"The supervisor exceeded 4 iterations ({last_code}: {last_message}).",
                    iterations=MAX_SUPERVISOR_ITERATIONS,
                ) from None
            self.model_calls += 1
            try:
                completion = self._model.complete(self._request(request, feedback))
            except ModelProviderError as exc:
                last_code = "model_provider"
                last_message = exc.message
                feedback = last_message
                continue
            if (
                completion.output_tokens > OUTPUT_TOKEN_CEILING
                or estimate_tokens(completion.text) > OUTPUT_TOKEN_CEILING
            ):
                last_code = "output_token_ceiling"
                last_message = "The model output exceeded the output token ceiling."
                feedback = last_message
                continue
            payload = extract_json_object(completion.text)
            if payload is None:
                last_code = "invalid_plan"
                last_message = "The model output did not contain a JSON plan."
                feedback = last_message
                continue
            try:
                return validate_plan(payload)
            except PlanValidationError as exc:
                last_code = exc.code
                last_message = exc.message
                feedback = f"{exc.code}: {exc.message}"

    def _request(self, request: PlanningRequest, feedback: str) -> ModelCompletionRequest:
        user_payload: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "allowlist": sorted(SPECIALIST_ALLOWLIST),
        }
        if feedback:
            user_payload["validation_error"] = feedback
        system_prompt, user_prompt = fit_to_input_ceiling(
            _SYSTEM_PROMPT,
            json.dumps(user_payload, separators=(",", ":")),
        )
        return ModelCompletionRequest(
            model_id=self._model_id,
            prompt_version=SUPERVISOR_PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_input_tokens=INPUT_TOKEN_CEILING,
            max_output_tokens=OUTPUT_TOKEN_CEILING,
            permitted_tools=(),
            task_id=uuid4(),
            agent=AgentType.SUPERVISOR,
        )


def _wants_purchase(request: PlanningRequest) -> bool:
    if request.asks_for_purchase:
        return True
    text = request.text.casefold()
    return any(marker in text for marker in _PURCHASE_MARKERS)


def _require(value: str | None, message: str) -> str:
    if value is None or not value.strip():
        raise PlanValidationError("invalid_input", message)
    return value


def _reorder_document(request: PlanningRequest) -> dict[str, Any]:
    sku = _require(request.sku, "A reorder plan needs a sku.")
    region = _require(request.region, "A reorder plan needs a region.")
    tasks = [
        _task("inventory", "inventory", {"sku": sku, "region": region}),
        _task(
            "demand",
            "demand",
            {"sku": sku, "region": region, "horizon_days": request.horizon_days},
        ),
        _task("supplier", "supplier", {"sku": sku}),
        _task("finance", "finance", {}),
        _task("policy", "policy", {"subject": "reorder"}),
    ]
    dependencies = [
        _edge("finance", "inventory"),
        _edge("finance", "demand"),
        _edge("finance", "supplier"),
        _edge("policy", "finance"),
        _edge("policy", "supplier"),
    ]
    return {"tasks": tasks, "dependencies": dependencies, "reason": _REORDER_REASON}


def _delay_document(request: PlanningRequest) -> dict[str, Any]:
    supplier_id = _require(request.supplier_id, "A supplier-delay plan needs a supplier.")
    tasks = [
        _task("supplier", "supplier", {"supplier_id": supplier_id}),
        _task(
            "demand",
            "demand",
            {"supplier_id": supplier_id, "horizon_days": request.horizon_days},
        ),
        _task("inventory", "inventory", {"supplier_id": supplier_id}),
        _task("policy", "policy", {"subject": "supplier_delay"}),
    ]
    dependencies = [
        _edge("demand", "supplier"),
        _edge("inventory", "supplier"),
        _edge("policy", "supplier"),
        _edge("policy", "demand"),
        _edge("policy", "inventory"),
    ]
    reason = _DELAY_REASON
    if _wants_purchase(request):
        tasks.append(_task("finance", "finance", {}))
        dependencies.extend([_edge("finance", "supplier"), _edge("finance", "demand")])
        dependencies.append(_edge("policy", "finance"))
        reason = _DELAY_PURCHASE_REASON
    return {"tasks": tasks, "dependencies": dependencies, "reason": reason}


def _budget_document(request: PlanningRequest) -> dict[str, Any]:
    if not request.candidates:
        raise PlanValidationError("invalid_input", "A budget plan needs candidates.")
    candidates = [item.model_dump(mode="json") for item in request.candidates]
    skus = [item.sku for item in request.candidates]
    tasks = [
        _task("finance", "finance", {}),
        _task("inventory", "inventory", {"candidates": candidates}),
        _task(
            "demand",
            "demand",
            {"candidates": candidates, "horizon_days": request.horizon_days},
        ),
        _task("supplier", "supplier", {"skus": skus}),
        _task("policy", "policy", {"subject": "budget_plan"}),
    ]
    dependencies = [
        _edge("policy", "finance"),
        _edge("policy", "inventory"),
        _edge("policy", "demand"),
        _edge("policy", "supplier"),
    ]
    return {"tasks": tasks, "dependencies": dependencies, "reason": _BUDGET_REASON}


def _incident_document(request: PlanningRequest) -> dict[str, Any]:
    if request.sku and request.region:
        inventory: dict[str, Any] = {"sku": request.sku, "region": request.region}
        demand: dict[str, Any] = {
            "sku": request.sku,
            "region": request.region,
            "horizon_days": request.horizon_days,
        }
    else:
        inventory = {"anomaly": True}
        demand = {"anomaly": True}
    supplier: dict[str, Any]
    if request.supplier_id:
        supplier = {"supplier_id": request.supplier_id}
    elif request.sku:
        supplier = {"sku": request.sku}
    else:
        supplier = {"anomaly": True}
    tasks = [
        _task("inventory", "inventory", inventory),
        _task("demand", "demand", demand),
        _task("supplier", "supplier", supplier),
        _task("policy", "policy", {"subject": "incident_analysis"}),
    ]
    dependencies = [
        _edge("policy", "inventory"),
        _edge("policy", "demand"),
        _edge("policy", "supplier"),
    ]
    return {"tasks": tasks, "dependencies": dependencies, "reason": _INCIDENT_REASON}


def _task(task_id: str, agent: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {"id": task_id, "agent": agent, "input": raw, "read_only": True}


def _edge(task_id: str, depends_on: str) -> dict[str, str]:
    return {"task_id": task_id, "depends_on": depends_on}
