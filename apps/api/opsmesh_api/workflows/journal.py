"""Store-backed journal. The coordinator reads task status only after commit."""

from datetime import datetime
from uuid import UUID, uuid4

from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.agents.coordinator import SpecialistRun
from opsmesh_api.agents.fanout import TrackedTask
from opsmesh_api.agents.plan_schema import SupervisorPlan
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import TaskStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import AgentResultCreate, EvidenceCreate
from opsmesh_api.workflows.store import WorkflowStore


class StoreJournal:
    """Persist each transition on the workflow store before it is read back."""

    def __init__(
        self,
        store: WorkflowStore,
        tenant_id: str,
        run_id: UUID,
        plan: SupervisorPlan,
        ids: dict[str, UUID],
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._plan = plan
        self._ids = ids

    def tasks(self) -> list[TrackedTask]:
        stored = {
            task.id: task for task in self._store.get_run(self._tenant_id, self._run_id).tasks
        }
        found: list[TrackedTask] = []
        for task in self._plan.tasks:
            row = stored[self._ids[task.id]]
            found.append(
                TrackedTask(
                    id=row.id,
                    plan_id=task.id,
                    agent=task.agent,
                    status=row.status,
                    depends_on=tuple(row.depends_on),
                    retry_count=row.retry_count,
                    plan_input=dict(task.input),
                )
            )
        return found

    def transition(self, task_id: UUID, status: TaskStatus, reason: str, at: datetime) -> None:
        self._store.transition_task(
            self._tenant_id,
            self._run_id,
            task_id,
            status=status,
            reason=reason,
            at=at,
        )

    def set_retry_count(self, task_id: UUID, count: int) -> None:
        self._store.set_retry_count(self._tenant_id, self._run_id, task_id, count)

    def commit_success(self, recorded: SpecialistRun) -> None:
        self._store.add_result(
            self._tenant_id,
            self._run_id,
            recorded.task.task_id,
            AgentResultCreate(
                id=uuid4(),
                schema_version=recorded.envelope.schema_version,
                facts=recorded.decision_facts or {},
                evidence=recorded.envelope.evidence,
                warnings=recorded.envelope.warnings,
                confidence=recorded.envelope.confidence,
                model_id=recorded.model_id,
                prompt_version=recorded.prompt_version,
            ),
        )
        if recorded.decision_facts is None or recorded.task.agent == AgentType.POLICY:
            return
        for item in recorded.envelope.evidence:
            self._store.add_evidence(
                self._tenant_id,
                self._run_id,
                EvidenceCreate(
                    id=uuid4(),
                    type=item.type,
                    source=item.source,
                    source_record_id=item.source_record_id,
                    observed_at=item.observed_at,
                    checksum=canonical_json_hash(recorded.decision_facts),
                ),
            )

    def commit_failure(self, recorded: SpecialistRun) -> None:
        """Store the failure envelope with empty facts so a guess cannot land."""

        self._store.add_result(
            self._tenant_id,
            self._run_id,
            recorded.task.task_id,
            AgentResultCreate(
                id=uuid4(),
                schema_version=recorded.envelope.schema_version,
                facts={},
                evidence=[],
                warnings=recorded.envelope.warnings,
                confidence=recorded.envelope.confidence,
                model_id=recorded.model_id,
                prompt_version=recorded.prompt_version,
            ),
        )

    def finish(self, status: WorkflowStatus, reason: str | None, at: datetime) -> None:
        del at
        self._store.update_run(
            self._tenant_id,
            self._run_id,
            status=status,
            reason=reason,
            write_reason=True,
        )
