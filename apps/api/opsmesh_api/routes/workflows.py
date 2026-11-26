"""Read and append workflow records for one tenant."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.routes.errors import call
from opsmesh_api.workflows.schemas import (
    AgentResultCreate,
    AgentResultRead,
    AgentTaskCreate,
    AgentTaskRead,
    ApprovalCreate,
    ApprovalRead,
    EvidenceCreate,
    EvidenceRead,
    ProposalCreate,
    ProposalRead,
    ToolExecutionCreate,
    ToolExecutionRead,
    WorkflowRunCreate,
    WorkflowRunList,
    WorkflowRunRead,
)
from opsmesh_api.workflows.store import WorkflowStore

router = APIRouter(prefix="/tenants/{tenant_id}/workflow-runs", tags=["workflows"])


def _store(db: Annotated[Session, Depends(get_db)]) -> WorkflowStore:
    return WorkflowStore(db)


@router.get("", response_model=WorkflowRunList)
def list_runs(
    tenant_id: str,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> WorkflowRunList:
    return call(lambda: WorkflowRunList(workflow_runs=store.list_runs(tenant_id)))


@router.post("", response_model=WorkflowRunRead)
def create_run(
    tenant_id: str,
    body: WorkflowRunCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> WorkflowRunRead:
    return call(lambda: store.create_run(tenant_id, body))


@router.get("/{run_id}", response_model=WorkflowRunRead)
def get_run(
    tenant_id: str,
    run_id: UUID,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> WorkflowRunRead:
    return call(lambda: store.get_run(tenant_id, run_id))


@router.post("/{run_id}/tasks", response_model=AgentTaskRead)
def add_task(
    tenant_id: str,
    run_id: UUID,
    body: AgentTaskCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> AgentTaskRead:
    return call(lambda: store.add_task(tenant_id, run_id, body))


@router.post("/{run_id}/tasks/{task_id}/results", response_model=AgentResultRead)
def add_result(
    tenant_id: str,
    run_id: UUID,
    task_id: UUID,
    body: AgentResultCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> AgentResultRead:
    return call(lambda: store.add_result(tenant_id, run_id, task_id, body))


@router.post("/{run_id}/evidence", response_model=EvidenceRead)
def add_evidence(
    tenant_id: str,
    run_id: UUID,
    body: EvidenceCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> EvidenceRead:
    return call(lambda: store.add_evidence(tenant_id, run_id, body))


@router.post("/{run_id}/proposals", response_model=ProposalRead)
def add_proposal(
    tenant_id: str,
    run_id: UUID,
    body: ProposalCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> ProposalRead:
    return call(lambda: store.add_proposal(tenant_id, run_id, body))


@router.post("/{run_id}/proposals/{proposal_id}/approvals", response_model=ApprovalRead)
def add_approval(
    tenant_id: str,
    run_id: UUID,
    proposal_id: UUID,
    body: ApprovalCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> ApprovalRead:
    return call(lambda: store.add_approval(tenant_id, run_id, proposal_id, body))


@router.post("/{run_id}/tasks/{task_id}/tool-executions", response_model=ToolExecutionRead)
def add_tool_execution(
    tenant_id: str,
    run_id: UUID,
    task_id: UUID,
    body: ToolExecutionCreate,
    store: Annotated[WorkflowStore, Depends(_store)],
) -> ToolExecutionRead:
    return call(lambda: store.add_tool_execution(tenant_id, run_id, task_id, body))
