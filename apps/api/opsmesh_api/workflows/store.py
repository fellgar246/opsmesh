"""Insert and tenant-scoped reads for workflow records."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.agents.plan_schema import SupervisorPlan, validate_plan
from opsmesh_api.services.models import Tenant
from opsmesh_api.workflows.errors import PersistenceRejected, WorkflowNotFound
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import (
    AgentResult,
    AgentTask,
    Approval,
    Evidence,
    OperationalProposal,
    ToolExecution,
    WorkflowRun,
)
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
    ResultEvidenceItem,
    ToolExecutionCreate,
    ToolExecutionRead,
    WarningItem,
    WorkflowRunCreate,
    WorkflowRunRead,
)


class WorkflowStore:
    """System of record. Callers pass schema-validated documents."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_run(self, tenant_id: str, body: WorkflowRunCreate) -> WorkflowRunRead:
        self._require_tenant(tenant_id)
        stored_plan = None
        if body.plan is not None:
            stored_plan = validate_plan(body.plan.model_dump(mode="json")).model_dump(mode="json")
        row = WorkflowRun(
            id=body.id,
            tenant_id=tenant_id,
            workflow_type=body.workflow_type,
            requested_by=body.requested_by,
            status=body.status.value,
            input=body.input,
            started_at=body.started_at,
            finished_at=body.finished_at,
            cost_summary=body.cost_summary.model_dump(mode="json"),
            plan=stored_plan,
        )
        self._insert(row)
        return self.get_run(tenant_id, body.id)

    def add_task(self, tenant_id: str, run_id: UUID, body: AgentTaskCreate) -> AgentTaskRead:
        self._require_run(tenant_id, run_id)
        if body.parent_task_id is not None:
            parent = self._task(tenant_id, run_id, body.parent_task_id)
            if parent is None:
                raise PersistenceRejected("parent task does not exist on this run")
        for dependency_id in body.depends_on:
            if self._task(tenant_id, run_id, dependency_id) is None:
                raise PersistenceRejected("dependency task does not exist on this run")
        row = AgentTask(
            id=body.id,
            tenant_id=tenant_id,
            workflow_run_id=run_id,
            agent_type=body.agent_type,
            parent_task_id=body.parent_task_id,
            depends_on=[str(item) for item in body.depends_on],
            status=body.status.value,
            input_hash=canonical_json_hash(body.input),
            started_at=body.started_at,
            finished_at=body.finished_at,
        )
        self._insert(row)
        stored = self._task(tenant_id, run_id, body.id)
        assert stored is not None
        return self._task_read(stored, [], [])

    def add_result(
        self,
        tenant_id: str,
        run_id: UUID,
        task_id: UUID,
        body: AgentResultCreate,
    ) -> AgentResultRead:
        if self._task(tenant_id, run_id, task_id) is None:
            raise PersistenceRejected("result task does not exist")
        row = AgentResult(
            id=body.id,
            tenant_id=tenant_id,
            task_id=task_id,
            schema_version=body.schema_version,
            facts=body.facts,
            evidence=[item.model_dump(mode="json") for item in body.evidence],
            warnings=[item.model_dump(mode="json") for item in body.warnings],
            confidence=body.confidence.value,
            model_id=body.model_id,
            prompt_version=body.prompt_version,
        )
        self._insert(row)
        return AgentResultRead(
            id=row.id,
            tenant_id=row.tenant_id,
            task_id=row.task_id,
            schema_version=row.schema_version,
            facts=row.facts,
            evidence=[ResultEvidenceItem.model_validate(item) for item in row.evidence],
            warnings=[WarningItem.model_validate(item) for item in row.warnings],
            confidence=body.confidence,
            model_id=row.model_id,
            prompt_version=row.prompt_version,
        )

    def add_evidence(
        self,
        tenant_id: str,
        run_id: UUID,
        body: EvidenceCreate,
    ) -> EvidenceRead:
        self._require_run(tenant_id, run_id)
        row = Evidence(
            id=body.id,
            tenant_id=tenant_id,
            workflow_run_id=run_id,
            type=body.type,
            source=body.source,
            source_record_id=body.source_record_id,
            observed_at=body.observed_at,
            checksum=body.checksum,
        )
        self._insert(row)
        return EvidenceRead(
            id=row.id,
            tenant_id=row.tenant_id,
            workflow_run_id=row.workflow_run_id,
            type=row.type,
            source=row.source,
            source_record_id=row.source_record_id,
            observed_at=row.observed_at,
            checksum=row.checksum,
        )

    def add_proposal(
        self,
        tenant_id: str,
        run_id: UUID,
        body: ProposalCreate,
    ) -> ProposalRead:
        self._require_run(tenant_id, run_id)
        row = OperationalProposal(
            id=body.id,
            tenant_id=tenant_id,
            workflow_run_id=run_id,
            proposal_type=body.proposal_type,
            arguments=body.arguments,
            estimated_impact=body.estimated_impact,
            risk_level=body.risk_level.value,
            status=body.status.value,
            created_at=body.created_at,
        )
        self._insert(row)
        return self._proposal_read(row, [])

    def add_approval(
        self,
        tenant_id: str,
        run_id: UUID,
        proposal_id: UUID,
        body: ApprovalCreate,
    ) -> ApprovalRead:
        proposal = self._db.scalar(
            select(OperationalProposal).where(
                OperationalProposal.id == proposal_id,
                OperationalProposal.tenant_id == tenant_id,
                OperationalProposal.workflow_run_id == run_id,
            )
        )
        if proposal is None:
            raise PersistenceRejected("proposal does not exist")
        row = Approval(
            id=body.id,
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            arguments_hash=canonical_json_hash(body.arguments),
            required_role=body.required_role.value,
            status=body.status.value,
            expires_at=body.expires_at,
            approved_by=body.approved_by,
        )
        self._insert(row)
        return self._approval_read(row)

    def add_tool_execution(
        self,
        tenant_id: str,
        run_id: UUID,
        task_id: UUID,
        body: ToolExecutionCreate,
    ) -> ToolExecutionRead:
        if self._task(tenant_id, run_id, task_id) is None:
            raise PersistenceRejected("tool task does not exist")
        row = ToolExecution(
            id=body.id,
            tenant_id=tenant_id,
            task_id=task_id,
            tool_name=body.tool_name,
            arguments_hash=canonical_json_hash(body.arguments),
            status=body.status.value,
            latency_ms=body.latency_ms,
            retry_count=body.retry_count,
            result_reference=body.result_reference,
        )
        self._insert(row)
        return self._tool_read(row)

    def list_runs(self, tenant_id: str) -> list[WorkflowRunRead]:
        """Return persisted runs for one tenant, newest start time first."""

        self._require_tenant(tenant_id)
        runs = self._db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.tenant_id == tenant_id)
            .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        ).all()
        return [self.get_run(tenant_id, run.id) for run in runs]

    def get_run(self, tenant_id: str, run_id: UUID) -> WorkflowRunRead:
        run = self._db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise WorkflowNotFound()
        tasks = self._db.scalars(
            select(AgentTask)
            .where(AgentTask.tenant_id == tenant_id, AgentTask.workflow_run_id == run_id)
            .order_by(AgentTask.id)
        ).all()
        task_ids = [task.id for task in tasks]
        results = (
            self._db.scalars(
                select(AgentResult)
                .where(AgentResult.tenant_id == tenant_id, AgentResult.task_id.in_(task_ids))
                .order_by(AgentResult.id)
            ).all()
            if task_ids
            else []
        )
        tools = (
            self._db.scalars(
                select(ToolExecution)
                .where(ToolExecution.tenant_id == tenant_id, ToolExecution.task_id.in_(task_ids))
                .order_by(ToolExecution.id)
            ).all()
            if task_ids
            else []
        )
        evidence = self._db.scalars(
            select(Evidence)
            .where(Evidence.tenant_id == tenant_id, Evidence.workflow_run_id == run_id)
            .order_by(Evidence.id)
        ).all()
        proposals = self._db.scalars(
            select(OperationalProposal)
            .where(
                OperationalProposal.tenant_id == tenant_id,
                OperationalProposal.workflow_run_id == run_id,
            )
            .order_by(OperationalProposal.id)
        ).all()
        proposal_ids = [proposal.id for proposal in proposals]
        approvals = (
            self._db.scalars(
                select(Approval)
                .where(Approval.tenant_id == tenant_id, Approval.proposal_id.in_(proposal_ids))
                .order_by(Approval.id)
            ).all()
            if proposal_ids
            else []
        )
        results_by_task: dict[UUID, list[AgentResultRead]] = {}
        for result in results:
            results_by_task.setdefault(result.task_id, []).append(self._result_read(result))
        tools_by_task: dict[UUID, list[ToolExecutionRead]] = {}
        for tool in tools:
            tools_by_task.setdefault(tool.task_id, []).append(self._tool_read(tool))
        approvals_by_proposal: dict[UUID, list[ApprovalRead]] = {}
        for approval in approvals:
            approvals_by_proposal.setdefault(approval.proposal_id, []).append(
                self._approval_read(approval)
            )
        return WorkflowRunRead(
            id=run.id,
            tenant_id=run.tenant_id,
            workflow_type=run.workflow_type,
            requested_by=run.requested_by,
            status=run.status,  # type: ignore[arg-type]
            input=run.input,
            started_at=run.started_at,
            finished_at=run.finished_at,
            cost_summary=run.cost_summary,  # type: ignore[arg-type]
            plan=None if run.plan is None else SupervisorPlan.model_validate(run.plan),
            tasks=[
                self._task_read(
                    task,
                    results_by_task.get(task.id, []),
                    tools_by_task.get(task.id, []),
                )
                for task in tasks
            ],
            evidence=[
                EvidenceRead(
                    id=item.id,
                    tenant_id=item.tenant_id,
                    workflow_run_id=item.workflow_run_id,
                    type=item.type,
                    source=item.source,
                    source_record_id=item.source_record_id,
                    observed_at=item.observed_at,
                    checksum=item.checksum,
                )
                for item in evidence
            ],
            proposals=[
                self._proposal_read(proposal, approvals_by_proposal.get(proposal.id, []))
                for proposal in proposals
            ],
        )

    def _require_tenant(self, tenant_id: str) -> None:
        if self._db.get(Tenant, tenant_id) is None:
            raise PersistenceRejected("tenant does not exist")

    def _require_run(self, tenant_id: str, run_id: UUID) -> WorkflowRun:
        run = self._db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise PersistenceRejected("workflow run does not exist")
        return run

    def _task(self, tenant_id: str, run_id: UUID, task_id: UUID) -> AgentTask | None:
        return self._db.scalar(
            select(AgentTask).where(
                AgentTask.id == task_id,
                AgentTask.tenant_id == tenant_id,
                AgentTask.workflow_run_id == run_id,
            )
        )

    def _insert(self, row: object) -> None:
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)

    def _task_read(
        self,
        task: AgentTask,
        results: list[AgentResultRead],
        tools: list[ToolExecutionRead],
    ) -> AgentTaskRead:
        return AgentTaskRead(
            id=task.id,
            tenant_id=task.tenant_id,
            workflow_run_id=task.workflow_run_id,
            agent_type=task.agent_type,
            parent_task_id=task.parent_task_id,
            depends_on=task.depends_on,  # type: ignore[arg-type]
            status=task.status,  # type: ignore[arg-type]
            input_hash=task.input_hash,
            started_at=task.started_at,
            finished_at=task.finished_at,
            results=results,
            tool_executions=tools,
        )

    def _result_read(self, row: AgentResult) -> AgentResultRead:
        return AgentResultRead(
            id=row.id,
            tenant_id=row.tenant_id,
            task_id=row.task_id,
            schema_version=row.schema_version,
            facts=row.facts,
            evidence=[ResultEvidenceItem.model_validate(item) for item in row.evidence],
            warnings=[WarningItem.model_validate(item) for item in row.warnings],
            confidence=row.confidence,  # type: ignore[arg-type]
            model_id=row.model_id,
            prompt_version=row.prompt_version,
        )

    def _tool_read(self, row: ToolExecution) -> ToolExecutionRead:
        return ToolExecutionRead(
            id=row.id,
            tenant_id=row.tenant_id,
            task_id=row.task_id,
            tool_name=row.tool_name,
            arguments_hash=row.arguments_hash,
            status=row.status,  # type: ignore[arg-type]
            latency_ms=row.latency_ms,
            retry_count=row.retry_count,
            result_reference=row.result_reference,
        )

    def _approval_read(self, row: Approval) -> ApprovalRead:
        return ApprovalRead(
            id=row.id,
            tenant_id=row.tenant_id,
            proposal_id=row.proposal_id,
            arguments_hash=row.arguments_hash,
            required_role=row.required_role,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            expires_at=row.expires_at,
            approved_by=row.approved_by,
        )

    def _proposal_read(
        self,
        row: OperationalProposal,
        approvals: list[ApprovalRead],
    ) -> ProposalRead:
        return ProposalRead(
            id=row.id,
            tenant_id=row.tenant_id,
            workflow_run_id=row.workflow_run_id,
            proposal_type=row.proposal_type,
            arguments=row.arguments,
            estimated_impact=row.estimated_impact,
            risk_level=row.risk_level,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            created_at=row.created_at,
            approvals=approvals,
        )
