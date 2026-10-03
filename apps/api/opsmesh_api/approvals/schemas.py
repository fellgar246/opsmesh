"""Approval queue and decision payloads."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh_api.workflows.models import ApprovalRole, ApprovalStatus, ProposalStatus


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalDecision(APIModel):
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def reject_requires_comment(self) -> Self:
        if self.decision == "reject" and not (self.comment and self.comment.strip()):
            raise ValueError("A comment is required to reject")
        if self.comment is not None:
            self.comment = self.comment.strip()
        return self


class ApprovalView(APIModel):
    id: UUID
    tenant_id: str
    proposal_id: UUID
    workflow_run_id: UUID
    arguments_hash: str
    live_arguments_hash: str
    required_role: ApprovalRole
    status: ApprovalStatus
    expires_at: datetime
    approved_by: str | None
    proposal_status: ProposalStatus
    quantity: int | None
    sku: str | None
    supplier_id: str | None
    extended_cost_cents: int | None
    requested_at: datetime
    workflow_type: str
    requested_by: str


class ApprovalQueue(APIModel):
    approvals: list[ApprovalView]


class ApprovalDetail(ApprovalView):
    proposal_approvals: list[ApprovalView]
