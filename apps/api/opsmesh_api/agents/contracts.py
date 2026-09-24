"""Versioned specialist task and result contract.

The coordinator accepts only this envelope. Prose is not a decision payload.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import ResultEvidenceItem, WarningItem

SCHEMA_VERSION = "1"


class AgentType(StrEnum):
    INVENTORY = "inventory"
    DEMAND = "demand"
    SUPPLIER = "supplier"
    FINANCE = "finance"
    POLICY = "policy"
    SUPERVISOR = "supervisor"


class AgentResultStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class EvidenceRef(BaseModel):
    """A pointer to evidence already accepted into the workflow."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    observed_at: datetime


class WorkflowContext(BaseModel):
    """Structured shared state for one run. This is not a transcript."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    request: dict[str, Any]
    facts: dict[str, dict[str, Any]]
    evidence_refs: list[EvidenceRef]
    completed_tasks: list[UUID]
    open_questions: list[str]
    proposals: list[dict[str, Any]]


class AgentTask(BaseModel):
    """One specialist invocation and the minimum context it may see."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    agent: AgentType
    tenant_id: str = Field(min_length=1)
    parameters: dict[str, Any]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class AgentResultEnvelope(BaseModel):
    """Result returned by every specialist transport."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    agent: AgentType
    task_id: UUID
    status: AgentResultStatus
    evidence: list[ResultEvidenceItem]
    facts: dict[str, Any]
    warnings: list[WarningItem]
    confidence: Confidence


class AgentClient(Protocol):
    """Port for a single specialist task. Transports implement this method."""

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        """Run one task and return a validated result envelope."""


class InventoryFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    region: str
    available_units: int
    reserved_units: int
    on_hand_units: int


class DemandFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    region: str
    horizon_days: int
    p50: int
    p90: int


class SupplierFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    supplier_id: str
    status: str
    lead_time_days: int
    minimum_order_quantity: int
    case_pack: int
    unit_cost_cents: int
    reliability_score: float


class FinanceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_cents: int
    committed_cents: int


class PolicyFacts(BaseModel):
    """Deterministic policy decision. `explanation` does not authorize a purchase."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None
    blocked: bool
    selected_quantile: str | None = None
    selected_demand: int | None = None
    gross_need: int | None = None
    quantity: int
    unit_cost_cents: int
    extended_cost_cents: int
    requires_manager_approval: bool
    requires_finance_approval: bool
    explanation: str | None = None


FACT_MODELS: dict[AgentType, type[BaseModel]] = {
    AgentType.INVENTORY: InventoryFacts,
    AgentType.DEMAND: DemandFacts,
    AgentType.SUPPLIER: SupplierFacts,
    AgentType.FINANCE: FinanceFacts,
    AgentType.POLICY: PolicyFacts,
}

PARAMETER_FIELDS: dict[AgentType, frozenset[str]] = {
    AgentType.INVENTORY: frozenset({"sku", "region"}),
    AgentType.DEMAND: frozenset({"sku", "region", "horizon_days"}),
    AgentType.SUPPLIER: frozenset({"sku"}),
    AgentType.FINANCE: frozenset(),
    AgentType.POLICY: frozenset(
        {
            "sku",
            "extended_cost_cents",
            "quantity",
            "unit_cost_cents",
            "supplier_status",
            "reason",
            "blocked",
            "selected_quantile",
            "selected_demand",
            "gross_need",
        }
    ),
    AgentType.SUPERVISOR: frozenset(),
}


class ContractRejection(Exception):
    """The envelope cannot be trusted and must not enter workflow facts."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class AcceptedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: AgentResultEnvelope
    facts: dict[str, Any]
    accepted: bool


def specialist_task(
    *,
    task_id: UUID,
    agent: AgentType,
    tenant_id: str,
    parameters: dict[str, Any],
    context: WorkflowContext,
) -> AgentTask:
    """Copy only this specialist's parameters and its own evidence references."""

    allowed = PARAMETER_FIELDS[agent]
    sliced = {key: value for key, value in parameters.items() if key in allowed}
    refs = [ref for ref in context.evidence_refs if ref.source == agent.value]
    return AgentTask(
        task_id=task_id,
        agent=agent,
        tenant_id=tenant_id,
        parameters=sliced,
        evidence_refs=refs,
    )


def approvals_from_policy(facts: dict[str, Any]) -> tuple[bool, bool]:
    """Read authorization from policy facts. The explanation string is ignored."""

    return bool(facts["requires_manager_approval"]), bool(facts["requires_finance_approval"])


def accept_result(task: AgentTask, payload: AgentResultEnvelope | dict[str, Any]) -> AcceptedResult:
    """Validate one envelope. Rejected facts are not returned as decision facts."""

    if isinstance(payload, AgentResultEnvelope):
        raw: dict[str, Any] = payload.model_dump(mode="python")
    else:
        raw = dict(payload)
    if "facts" not in raw:
        raise ContractRejection("missing_facts", "The result omitted facts.")
    agent_name = raw.get("agent")
    if not isinstance(agent_name, str) or agent_name not in {item.value for item in AgentType}:
        raise ContractRejection("unknown_agent", "The result names an unknown agent.")
    try:
        envelope = AgentResultEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise ContractRejection("invalid_envelope", "The result envelope is invalid.") from exc
    if envelope.schema_version != SCHEMA_VERSION:
        raise ContractRejection(
            "schema_version_mismatch",
            "The result schema version is not supported.",
        )
    if envelope.agent != task.agent:
        raise ContractRejection("unknown_agent", "The result names an unexpected agent.")
    if envelope.status != AgentResultStatus.COMPLETED:
        return AcceptedResult(envelope=envelope, facts={}, accepted=False)
    model = FACT_MODELS.get(envelope.agent)
    if model is None:
        raise ContractRejection("unknown_agent", "The result names an unknown agent.")
    unknown = sorted(key for key in envelope.facts if key not in model.model_fields)
    if unknown:
        names = ", ".join(unknown)
        raise ContractRejection("ignored_fact", f"Ignored undeclared fact '{names}'.")
    try:
        decision = model.model_validate(envelope.facts)
    except ValidationError as exc:
        raise ContractRejection(
            "invalid_facts",
            "The result facts do not match the agent schema.",
        ) from exc
    return AcceptedResult(
        envelope=envelope,
        facts=decision.model_dump(mode="json"),
        accepted=True,
    )
