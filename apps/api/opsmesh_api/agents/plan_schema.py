"""Supervisor plan schema and the checks that decide whether it can run."""

import json
import re
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from opsmesh_api.agents.ceilings import MAX_AGENTS, MAX_DELEGATIONS, MAX_TASKS

# Specialists a plan may name. User text cannot add to this set.
SPECIALIST_ALLOWLIST = frozenset({"inventory", "demand", "supplier", "finance", "policy"})

_ENGLISH_REASON = re.compile(r"^[A-Za-z0-9 ,.'’:/()-]+$")
_FORBIDDEN_INPUT_KEYS = frozenset({"tool", "tools", "sql", "query", "table", "statement"})
_WRITE_TOOL_MARKERS = ("procurement.create", "procurement.draft", "procurement.write")


class PlanValidationError(Exception):
    """A plan was rejected before any runnable task could be stored."""

    def __init__(self, code: str, message: str, iterations: int = 0) -> None:
        self.code = code
        self.message = message
        self.iterations = iterations
        super().__init__(message)


class CandidateInput(BaseModel):
    """One sku and region named by the request. This is not a database row."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    region: str = Field(min_length=1)


class _ScopedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str | None = Field(default=None, min_length=1)
    region: str | None = Field(default=None, min_length=1)
    supplier_id: str | None = Field(default=None, min_length=1)
    candidates: list[CandidateInput] | None = None
    skus: list[str] | None = None
    anomaly: bool | None = None
    horizon_days: int | None = Field(default=None, ge=1)
    subject: str | None = None

    @model_validator(mode="after")
    def lists_are_present(self) -> Self:
        if self.skus is not None and (not self.skus or any(not item for item in self.skus)):
            raise ValueError("skus must be a non-empty list")
        if self.candidates is not None and not self.candidates:
            raise ValueError("candidates must be a non-empty list")
        return self


class InventoryPlanInput(_ScopedInput):
    """Typed inventory task input known before any specialist runs."""

    @model_validator(mode="after")
    def has_scope(self) -> Self:
        if (self.sku is None) ^ (self.region is None):
            raise ValueError("inventory input needs both sku and region")
        if self.sku or self.supplier_id or self.candidates or self.anomaly:
            return self
        raise ValueError("inventory input needs a scope")


class DemandPlanInput(_ScopedInput):
    """Typed demand task input known before any specialist runs."""

    @model_validator(mode="after")
    def has_scope(self) -> Self:
        if (self.sku is None) ^ (self.region is None):
            raise ValueError("demand input needs both sku and region")
        scoped = self.sku or self.supplier_id or self.candidates or self.anomaly
        if not scoped:
            raise ValueError("demand input needs a scope")
        if self.anomaly:
            return self
        if self.horizon_days is None:
            raise ValueError("demand input needs a horizon")
        return self


class SupplierPlanInput(_ScopedInput):
    """Typed supplier task input known before any specialist runs."""

    @model_validator(mode="after")
    def has_scope(self) -> Self:
        if self.sku or self.supplier_id or self.skus or self.anomaly:
            return self
        raise ValueError("supplier input needs a scope")


class FinancePlanInput(BaseModel):
    """Finance tasks carry no request fields. Facts arrive after dependencies."""

    model_config = ConfigDict(extra="forbid")


class PolicyPlanInput(BaseModel):
    """Policy tasks name the workflow subject. They do not carry a decision."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(pattern="^(reorder|supplier_delay|budget_plan|incident_analysis)$")


INPUT_MODELS: dict[str, type[BaseModel]] = {
    "inventory": InventoryPlanInput,
    "demand": DemandPlanInput,
    "supplier": SupplierPlanInput,
    "finance": FinancePlanInput,
    "policy": PolicyPlanInput,
}


class PlanTask(BaseModel):
    """One specialist task. The id is local to the plan."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    agent: str = Field(min_length=1, max_length=64)
    input: dict[str, Any]
    read_only: bool = True


class PlanDependency(BaseModel):
    """`task_id` cannot start until `depends_on` has finished."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    depends_on: str = Field(min_length=1)


class SupervisorPlan(BaseModel):
    """A validated specialist graph and the reason it was chosen."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[PlanTask]
    dependencies: list[PlanDependency]
    reason: str = Field(min_length=1, max_length=500)


def validate_plan(payload: Any) -> SupervisorPlan:
    """Accept a plan object. Rejection happens before any task is stored."""

    if not isinstance(payload, dict):
        raise PlanValidationError("invalid_plan", "The plan must be a JSON object.")
    try:
        draft = SupervisorPlan.model_validate(payload)
    except ValidationError as exc:
        raise PlanValidationError("invalid_plan", "The plan does not match the schema.") from exc
    _reject_limits(draft)
    _reject_unknown_agents(draft)
    _reject_writes_and_tools(draft)
    normalized = _normalize_tasks(draft)
    _reject_duplicate_tasks(normalized)
    _reject_broken_edges(normalized)
    return normalized


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object that has the plan fields. Prose is ignored."""

    start = 0
    while start < len(text):
        brace = text.find("{", start)
        if brace < 0:
            return None
        parsed = _slice_object(text, brace)
        if (
            isinstance(parsed, dict)
            and "tasks" in parsed
            and "dependencies" in parsed
            and "reason" in parsed
        ):
            return parsed
        start = brace + 1
    return None


def parent_agents(plan: SupervisorPlan, agent: str) -> list[str]:
    """Return the agent types one agent waits on, when the plan has one such task."""

    matches = [task for task in plan.tasks if task.agent == agent]
    if len(matches) != 1:
        return []
    task_id = matches[0].id
    parents: list[str] = []
    for edge in plan.dependencies:
        if edge.task_id != task_id:
            continue
        parent = next((task.agent for task in plan.tasks if task.id == edge.depends_on), None)
        if parent is not None:
            parents.append(parent)
    return parents


def _reject_limits(plan: SupervisorPlan) -> None:
    if len(plan.tasks) > MAX_TASKS:
        raise PlanValidationError("task_limit", "The plan has more than 12 tasks.")
    agents = {task.agent for task in plan.tasks}
    if len(agents) > MAX_AGENTS:
        raise PlanValidationError("agent_limit", "The plan has more than 6 agents.")
    if len(plan.dependencies) > MAX_DELEGATIONS:
        raise PlanValidationError(
            "delegation_limit",
            "The plan has more than 10 delegations.",
        )


def _reject_unknown_agents(plan: SupervisorPlan) -> None:
    for task in plan.tasks:
        if task.agent not in SPECIALIST_ALLOWLIST:
            raise PlanValidationError("unknown_agent", "The plan names an unknown agent.")


def _reject_writes_and_tools(plan: SupervisorPlan) -> None:
    for task in plan.tasks:
        if not task.read_only:
            raise PlanValidationError("write_task", "The plan includes a write task.")
        if _contains_forbidden(task.input):
            raise PlanValidationError("forbidden_tool", "The plan includes a database tool.")


def _normalize_tasks(plan: SupervisorPlan) -> SupervisorPlan:
    if not _ENGLISH_REASON.fullmatch(plan.reason) or not re.search(r"[A-Za-z]", plan.reason):
        raise PlanValidationError("invalid_reason", "The plan reason must be English.")
    tasks: list[PlanTask] = []
    for task in plan.tasks:
        model = INPUT_MODELS[task.agent]
        try:
            typed = model.model_validate(task.input)
        except ValidationError as exc:
            raise PlanValidationError(
                "invalid_input",
                "A task input does not match the agent schema.",
            ) from exc
        tasks.append(
            task.model_copy(
                update={"input": typed.model_dump(mode="json", exclude_none=True)},
            )
        )
    return plan.model_copy(update={"tasks": tasks})


def _reject_duplicate_tasks(plan: SupervisorPlan) -> None:
    seen_ids: set[str] = set()
    seen_inputs: set[tuple[str, str]] = set()
    for task in plan.tasks:
        if task.id in seen_ids:
            raise PlanValidationError("duplicate_task", "The plan repeats a task id.")
        seen_ids.add(task.id)
        fingerprint = (task.agent, _canonical(task.input))
        if fingerprint in seen_inputs:
            raise PlanValidationError(
                "duplicate_task",
                "The plan repeats an agent task with equivalent input.",
            )
        seen_inputs.add(fingerprint)
    seen_edges: set[tuple[str, str]] = set()
    for edge in plan.dependencies:
        key = (edge.task_id, edge.depends_on)
        if key in seen_edges:
            raise PlanValidationError("duplicate_dependency", "The plan repeats a dependency.")
        seen_edges.add(key)


def _reject_broken_edges(plan: SupervisorPlan) -> None:
    ids = {task.id for task in plan.tasks}
    graph: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for edge in plan.dependencies:
        if edge.task_id not in ids or edge.depends_on not in ids:
            raise PlanValidationError(
                "missing_task",
                "A dependency names a task that is not in the plan.",
            )
        graph[edge.task_id].append(edge.depends_on)
    visiting: set[str] = set()
    visited: set[str] = set()

    def _cycle(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(_cycle(parent) for parent in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    if any(_cycle(node) for node in graph):
        raise PlanValidationError("cycle", "The plan graph is cyclic.")


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key) in _FORBIDDEN_INPUT_KEYS for key in value):
            return True
        return any(_contains_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        folded = value.casefold()
        return any(marker in folded for marker in _WRITE_TOOL_MARKERS)
    return False


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _slice_object(text: str, start: int) -> Any:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None
