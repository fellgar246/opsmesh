"""Golden workflows and the scores recorded for one evaluation run."""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh_api.services.schemas import APIModel

SUITE_VERSION = "1"

CATEGORIES: tuple[str, ...] = (
    "normal_reorder",
    "insufficient_budget",
    "supplier_minimum_order_conflict",
    "unapproved_supplier",
    "forecast_uncertainty",
    "missing_inventory",
    "supplier_delay",
    "conflicting_evidence",
    "policy_approval_required",
    "cross_tenant_access",
    "prompt_injection",
    "tool_failure",
    "agent_timeout",
    "partial_workflow_failure",
)

Gate = Literal["smoke", "full", "integration"]
WorkflowKindName = Literal["reorder", "supplier_delay"]
FaultName = Literal["tool_failure", "agent_timeout", "partial_failure"]
SUCCESS_STATUSES = frozenset({"COMPLETED", "WAITING_APPROVAL"})


class CeilingExpectation(APIModel):
    """Hard limits the workflow must stay inside."""

    max_agents: int = Field(default=6, ge=1)
    max_tasks: int = Field(default=12, ge=1)
    max_model_calls: int = Field(default=20, ge=0)
    max_tool_calls: int = Field(default=30, ge=0)
    max_latency_ms: int = Field(default=180_000, ge=0)


class ExpectedOutcome(APIModel):
    """Structured outcome a golden workflow must match."""

    status: str = Field(min_length=1)
    reason: str | None = None
    agents: list[str]
    tools: list[str]
    forbidden_tools: list[str]
    facts: dict[str, Any] = Field(default_factory=dict)
    quantity: int | None = None
    requires_manager_approval: bool = False
    requires_finance_approval: bool = False
    approval_roles: list[str] = Field(default_factory=list)
    binding_constraints: list[str] | None = None
    task_statuses: dict[str, str] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    ceilings: CeilingExpectation = Field(default_factory=CeilingExpectation)
    foreign_markers: list[str] = Field(default_factory=list)
    require_citations: bool = False
    rejected_quantities: list[int] = Field(default_factory=list)


class SetupOverlay(APIModel):
    """Temporary simulator edits applied before a case and restored after it."""

    available_cents: int | None = Field(default=None, ge=0)
    forecast_p50: int | None = Field(default=None, ge=0)
    forecast_p90: int | None = Field(default=None, ge=0)


class RecordedObservation(APIModel):
    """A stored model trace. Scoring it does not call a hosted model."""

    status: str
    reason: str | None = None
    agents: list[str]
    tools: list[str]
    facts: dict[str, Any] = Field(default_factory=dict)
    quantity: int | None = None
    requires_manager_approval: bool = False
    requires_finance_approval: bool = False
    approval_roles: list[str] = Field(default_factory=list)
    binding_constraints: list[str] = Field(default_factory=list)
    task_statuses: dict[str, str] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_cents: int | None = Field(default=None, ge=0)
    task_count: int = Field(ge=0)
    evidence_text: str = ""
    citation_checksums: list[str] = Field(default_factory=list)


class GoldenWorkflow(APIModel):
    """One versioned workflow: input, expected outcome, and tags."""

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    category: str
    gate: Gate
    tenant_id: str = Field(min_length=1)
    workflow_type: WorkflowKindName
    input: dict[str, Any]
    setup: SetupOverlay = Field(default_factory=SetupOverlay)
    fault: FaultName | None = None
    fault_agent: str | None = None
    expected: ExpectedOutcome
    recorded: RecordedObservation | None = None

    @model_validator(mode="after")
    def category_is_known(self) -> Self:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category}")
        if self.gate == "integration" and self.recorded is None:
            raise ValueError("integration cases need a recorded observation")
        if self.fault is not None and not self.fault_agent:
            raise ValueError("a fault needs a specialist")
        return self


class SuiteDocument(APIModel):
    """The checked-in golden set."""

    version: str = Field(min_length=1)
    workflows: list[GoldenWorkflow]

    @model_validator(mode="after")
    def ids_are_unique(self) -> Self:
        ids = [item.id for item in self.workflows]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow ids must be unique")
        return self


class FactAccuracy(APIModel):
    matched: int = Field(ge=0)
    total: int = Field(ge=0)


class DeterministicScores(APIModel):
    """Scores that stay the same when the same local suite is repeated."""

    correct_agents: int = Field(ge=0)
    unnecessary_agents: int = Field(ge=0)
    correct_tools: int = Field(ge=0)
    forbidden_tools: int = Field(ge=0)
    fact_accuracy: FactAccuracy
    constraint_violations: int = Field(ge=0)
    policy_violations: int = Field(ge=0)
    approval_correct: int = Field(ge=0)
    cross_tenant_failures: int = Field(ge=0)
    ceilings_respected: bool


class EfficiencyScores(APIModel):
    """Completion and spend for the workflows in one run."""

    task_completion: int = Field(ge=0)
    workflows: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_per_successful_workflow_cents: int | None = Field(default=None, ge=0)


class CaseScore(APIModel):
    id: str
    category: str
    tenant_id: str
    passed: bool
    failures: list[str]
    status: str
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_cents: int | None = Field(default=None, ge=0)


class EvaluationReport(APIModel):
    """One finished suite: version, per-metric scores, and cost."""

    suite_version: str
    suite: Literal["smoke", "full"]
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    deterministic: DeterministicScores
    efficiency: EfficiencyScores
    cases: list[CaseScore]
    hosted_ingested: bool = False


class EvaluationRunRead(APIModel):
    id: str
    tenant_id: str
    suite_version: str
    suite: str
    created_at: str
    passed: int
    failed: int
    deterministic: DeterministicScores
    efficiency: EfficiencyScores
    cases: list[CaseScore]
    hosted_ingested: bool = False


class EvaluationLatest(APIModel):
    run: EvaluationRunRead | None
    previous: EvaluationRunRead | None = None


class Observation(BaseModel):
    """What a case actually did. The scorer compares this to the golden outcome."""

    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None
    agents: list[str]
    tools: list[str]
    facts: dict[str, Any]
    quantity: int | None
    requires_manager_approval: bool
    requires_finance_approval: bool
    approval_roles: list[str]
    binding_constraints: list[str]
    task_statuses: dict[str, str]
    warning_codes: list[str]
    model_calls: int
    tool_calls: int
    latency_ms: int
    estimated_cost_cents: int | None
    task_count: int
    evidence_text: str
    citation_checksums: list[str]


class MissingFixtureError(Exception):
    """A listed workflow is not in the golden set. The suite does not skip it."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__("Missing evaluation fixtures: " + ", ".join(missing))


class LiveEvaluationError(Exception):
    """Live model evaluation is a separate opt-in and does not run by default."""
