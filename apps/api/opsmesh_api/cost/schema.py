"""Cost report returned for one tenant."""

from typing import Any
from uuid import UUID

from pydantic import Field

from opsmesh_api.workflows.schemas import APIModel


class UsageLedger(APIModel):
    """Calls, tokens, and estimated cost for one workflow."""

    workflow_id: UUID
    workflow_type: str
    input: dict[str, Any]
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_cents: int | None = Field(default=None, ge=0)


class CostReport(APIModel):
    """Daily start count and one ledger row per workflow."""

    workflows_started_today: int = Field(ge=0)
    daily_ceiling: int = Field(ge=1)
    workflows: list[UsageLedger]
