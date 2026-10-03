"""Execute a validated plan: independent tasks overlap, dependents wait."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any, Protocol
from uuid import UUID, uuid4

from opsmesh_api.agents.ceilings import (
    MAX_AGENTS,
    MAX_MODEL_CALLS,
    MAX_TASK_RETRIES,
    MAX_TOOL_CALLS,
    TASK_TIMEOUT_SECONDS,
    WORKFLOW_DEADLINE_SECONDS,
)
from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentType,
    ContractRejection,
    WorkflowContext,
    accept_result,
    approvals_from_policy,
    specialist_task,
)
from opsmesh_api.agents.coordinator import (
    ReorderCoordination,
    SpecialistRun,
    _accept_into_context,
    _accept_policy_explanation,
    _model_calls,
    _proposal_arguments,
    _provenance,
    _rejection_envelope,
    _tool_calls,
)
from opsmesh_api.agents.plan_schema import SupervisorPlan
from opsmesh_api.agents.transitions import UNSATISFIED, IllegalTransition, assert_transition
from opsmesh_api.operations.policy import REASON_MISSING_EVIDENCE
from opsmesh_api.operations.quantity import ReorderMath
from opsmesh_api.operations.schemas import ReorderRequest
from opsmesh_api.operations.synthesis import (
    ConstraintOutcome,
    accept_explanation,
    render_explanation,
    synthesize_from_facts,
)
from opsmesh_api.workflows.models import Confidence, TaskStatus, WorkflowStatus
from opsmesh_api.workflows.schemas import WarningItem

_RETRYABLE = frozenset({"task_timeout", "specialist_failed"})


@dataclass(frozen=True)
class TrackedTask:
    """A plan task as last committed. Scheduling reads this snapshot."""

    id: UUID
    plan_id: str
    agent: str
    status: TaskStatus
    depends_on: tuple[UUID, ...]
    retry_count: int
    plan_input: dict[str, Any]


@dataclass
class _MutableTask:
    id: UUID
    plan_id: str
    agent: str
    status: TaskStatus
    depends_on: tuple[UUID, ...]
    retry_count: int
    plan_input: dict[str, Any]


@dataclass
class _InFlight:
    task_id: UUID
    future: Future[AgentResultEnvelope]
    deadline: float
    abandoned: bool = False


class ExecutionJournal(Protocol):
    """Persistence the coordinator reads after every status change."""

    def tasks(self) -> list[TrackedTask]:
        """Return tasks from the last committed state."""

    def transition(self, task_id: UUID, status: TaskStatus, reason: str, at: datetime) -> None:
        """Commit one allowed status change."""

    def set_retry_count(self, task_id: UUID, count: int) -> None:
        """Commit the visible retry count for one task."""

    def commit_success(self, recorded: SpecialistRun) -> None:
        """Commit accepted facts before dependents are allowed to start."""

    def commit_failure(self, recorded: SpecialistRun) -> None:
        """Commit an empty failure envelope. Facts from the attempt are omitted."""

    def finish(self, status: WorkflowStatus, reason: str | None, at: datetime) -> None:
        """Commit the workflow status and reason."""


class MemoryJournal:
    """In-process journal for tests. Each change is recorded before it is visible."""

    def __init__(self, plan: SupervisorPlan) -> None:
        mapping = {task.id: uuid4() for task in plan.tasks}
        self._tasks: list[_MutableTask] = []
        for task in plan.tasks:
            depends = tuple(
                mapping[edge.depends_on] for edge in plan.dependencies if edge.task_id == task.id
            )
            self._tasks.append(
                _MutableTask(
                    id=mapping[task.id],
                    plan_id=task.id,
                    agent=task.agent,
                    status=TaskStatus.PENDING,
                    depends_on=depends,
                    retry_count=0,
                    plan_input=dict(task.input),
                )
            )
        self.history: list[dict[str, TaskStatus]] = []
        self.committed: dict[UUID, dict[str, Any]] = {}
        self.workflow_status = WorkflowStatus.RUNNING
        self.reason: str | None = None

    def tasks(self) -> list[TrackedTask]:
        return [_snapshot(task) for task in self._tasks]

    def transition(self, task_id: UUID, status: TaskStatus, reason: str, at: datetime) -> None:
        del reason, at
        task = self._mutable(task_id)
        assert_transition(task.status, status)
        task.status = status
        self.history.append({item.plan_id: item.status for item in self._tasks})

    def set_retry_count(self, task_id: UUID, count: int) -> None:
        if count > MAX_TASK_RETRIES:
            raise IllegalTransition(TaskStatus.FAILED, TaskStatus.READY)
        self._mutable(task_id).retry_count = count

    def commit_success(self, recorded: SpecialistRun) -> None:
        facts = recorded.decision_facts or {}
        self.committed[recorded.task.task_id] = dict(facts)

    def commit_failure(self, recorded: SpecialistRun) -> None:
        self.committed[recorded.task.task_id] = {}

    def finish(self, status: WorkflowStatus, reason: str | None, at: datetime) -> None:
        del at
        self.workflow_status = status
        self.reason = reason

    def _mutable(self, task_id: UUID) -> _MutableTask:
        for task in self._tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


def _snapshot(task: _MutableTask) -> TrackedTask:
    return TrackedTask(
        id=task.id,
        plan_id=task.plan_id,
        agent=task.agent,
        status=task.status,
        depends_on=task.depends_on,
        retry_count=task.retry_count,
        plan_input=dict(task.plan_input),
    )


class PlanCoordinator:
    """Run one validated specialist graph against an agent client."""

    def __init__(
        self,
        client: AgentClient,
        journal: ExecutionJournal,
        *,
        task_timeout_seconds: float = TASK_TIMEOUT_SECONDS,
        deadline_seconds: float = WORKFLOW_DEADLINE_SECONDS,
        monotonic_clock: Callable[[], float] | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        max_model_calls: int = MAX_MODEL_CALLS,
        max_retries: int = MAX_TASK_RETRIES,
    ) -> None:
        self._client = client
        self._journal = journal
        self._task_timeout = task_timeout_seconds
        self._deadline_seconds = deadline_seconds
        self._clock = monotonic if monotonic_clock is None else monotonic_clock
        self._max_tool_calls = max_tool_calls
        self._max_model_calls = max_model_calls
        self._max_retries = max_retries
        self._cancel = Event()
        self._pool: ThreadPoolExecutor | None = None
        self._inflight: dict[Future[AgentResultEnvelope], _InFlight] = {}
        self._inflight_ids: set[UUID] = set()
        self._closed = False
        self._deadline_at = 0.0
        self._settled: set[UUID] = set()
        self._limit_reason: str | None = None
        self._workflow_status = WorkflowStatus.RUNNING
        self._reason: str | None = None
        self._math: ReorderMath | None = None
        self._outcome: ConstraintOutcome | None = None
        self._explanation: str | None = None
        self._manager = False
        self._finance = False
        self._runs: dict[UUID, SpecialistRun] = {}
        self._tool_calls = 0
        self._model_calls = 0
        self._context: WorkflowContext | None = None
        self._request: ReorderRequest | None = None
        self._tenant_id = ""
        self._failed_envelopes: dict[UUID, AgentResultEnvelope] = {}

    def cancel(self) -> None:
        """Ask the coordinator to stop. Ready and running tasks are cancelled."""

        self._cancel.set()

    def run(
        self,
        *,
        workflow_id: UUID,
        tenant_id: str,
        request: ReorderRequest,
        plan: SupervisorPlan,
    ) -> ReorderCoordination:
        del plan
        self._tenant_id = tenant_id
        self._request = request
        self._context = WorkflowContext(
            workflow_id=workflow_id,
            request={
                "sku": request.sku,
                "region": request.region,
                "service_level": request.service_level,
            },
            facts={},
            evidence_refs=[],
            completed_tasks=[],
            open_questions=[],
            proposals=[],
        )
        self._sync_counts()
        started = self._clock()
        self._deadline_at = started + self._deadline_seconds
        with ThreadPoolExecutor(max_workers=MAX_AGENTS) as pool:
            self._pool = pool
            try:
                self._drive()
            finally:
                self._pool = None
                pool.shutdown(wait=False, cancel_futures=True)
        return self._result()

    def _drive(self) -> None:
        while not self._closed:
            if self._cancel.is_set():
                self._cancel_open()
                return
            if self._past_deadline():
                self._expire_workflow()
                return
            self._skip_blocked()
            self._launch()
            if self._closed:
                return
            if self._limit_reason and not self._inflight:
                self._fail_limited()
                return
            if not self._inflight:
                self._conclude()
                return
            self._wait_for_one()
            if self._closed:
                return

    def _launch(self) -> None:
        if self._limit_reason or self._pool is None:
            return
        armed: list[TrackedTask] = []
        while True:
            if self._cancel.is_set():
                self._cancel_open()
                return
            candidate = self._next_candidate()
            if candidate is None:
                break
            blocked = self._ceiling_block(len(armed))
            if blocked is not None:
                self._limit_reason = blocked
                break
            if self._parameters(candidate) is None:
                for ready in armed:
                    self._submit(ready)
                self._transition(candidate.id, TaskStatus.SKIPPED, "missing_evidence")
                self._finish(WorkflowStatus.FAILED, REASON_MISSING_EVIDENCE)
                return
            if candidate.status == TaskStatus.PENDING:
                self._transition(candidate.id, TaskStatus.READY, "dependencies_completed")
                if self._cancel.is_set():
                    if self._status(candidate.id) == TaskStatus.READY:
                        self._transition(candidate.id, TaskStatus.CANCELLED, "cancelled")
                    self._cancel_open()
                    return
            self._transition(candidate.id, TaskStatus.RUNNING, "started")
            armed.append(candidate)
        for task in armed:
            self._submit(task)

    def _next_candidate(self) -> TrackedTask | None:
        current = {task.id: task for task in self._journal.tasks()}
        for task in current.values():
            if task.id in self._inflight_ids:
                continue
            if task.status == TaskStatus.READY:
                return task
            if task.status != TaskStatus.PENDING:
                continue
            deps = [current[item] for item in task.depends_on]
            if all(dep.status == TaskStatus.COMPLETED for dep in deps):
                return task
        return None

    def _submit(self, task: TrackedTask) -> None:
        if self._pool is None:
            return
        parameters = self._parameters(task)
        if parameters is None:
            return
        context = self._context
        assert context is not None
        agent_task = specialist_task(
            task_id=task.id,
            agent=AgentType(task.agent),
            tenant_id=self._tenant_id,
            parameters=parameters,
            context=context,
        )
        future = self._pool.submit(self._call, agent_task)
        self._inflight[future] = _InFlight(
            task_id=task.id,
            future=future,
            deadline=self._clock() + self._task_timeout,
        )
        self._inflight_ids.add(task.id)

    def _parameters(self, task: TrackedTask) -> dict[str, Any] | None:
        if task.agent != AgentType.POLICY.value:
            return dict(task.plan_input)
        math = self._solve()
        if math is None or self._context is None:
            return None
        self._math = math
        supplier = self._context.facts[AgentType.SUPPLIER.value]
        request = self._request
        assert request is not None
        return {
            "sku": request.sku,
            "extended_cost_cents": math.extended_cost_cents,
            "quantity": math.quantity,
            "unit_cost_cents": math.unit_cost_cents,
            "supplier_status": supplier["status"],
            "reason": math.reason,
            "blocked": math.blocked,
            "selected_quantile": math.selected_quantile,
            "selected_demand": math.selected_demand,
            "gross_need": math.gross_need,
        }

    def _solve(self) -> ReorderMath | None:
        if self._context is None or self._request is None:
            return None
        facts = self._context.facts
        required = (
            AgentType.INVENTORY.value,
            AgentType.DEMAND.value,
            AgentType.SUPPLIER.value,
            AgentType.FINANCE.value,
        )
        if any(name not in facts for name in required):
            return None
        outcome = synthesize_from_facts(
            service_level=self._request.service_level,
            inventory=facts[AgentType.INVENTORY.value],
            demand=facts[AgentType.DEMAND.value],
            supplier=facts[AgentType.SUPPLIER.value],
            finance=facts[AgentType.FINANCE.value],
            evidence_refs=[
                (item.source, item.source_record_id) for item in self._context.evidence_refs
            ],
        )
        self._outcome = outcome
        if outcome.math is None:
            self._explanation = render_explanation(outcome)
            return None
        return outcome.math

    def _call(self, task: object) -> AgentResultEnvelope:
        from opsmesh_api.agents.contracts import AgentTask

        assert isinstance(task, AgentTask)
        try:
            return self._client.execute(task)
        except ContractRejection as error:
            return _rejection_envelope(task, error)
        except Exception:
            return _failure_envelope(
                task,
                "specialist_failed",
                "The specialist failed before it returned a result.",
            )

    def _wait_for_one(self) -> None:
        if not self._inflight:
            return
        remaining = self._deadline_at - self._clock()
        soonest = min(item.deadline for item in self._inflight.values())
        timeout = max(0.0, min(remaining, soonest - self._clock()))
        done, _pending = wait(
            set(self._inflight),
            timeout=timeout,
            return_when="FIRST_COMPLETED",
        )
        if self._past_deadline():
            self._expire_workflow()
            return
        for future in done:
            self._collect(future)
        now = self._clock()
        for item in list(self._inflight.values()):
            if now >= item.deadline:
                self._abandon(item, "task_timeout")

    def _collect(self, future: Future[AgentResultEnvelope]) -> None:
        item = self._inflight.pop(future, None)
        if item is None:
            return
        self._inflight_ids.discard(item.task_id)
        if item.abandoned or self._cancel.is_set():
            if self._status(item.task_id) == TaskStatus.RUNNING:
                self._transition(item.task_id, TaskStatus.CANCELLED, "cancelled")
            return
        try:
            payload = future.result()
        except Exception:
            self._fail_attempt(item.task_id, "specialist_failed", None)
            return
        self._sync_counts()
        recorded = self._recorded(item.task_id, payload)
        if recorded.decision_facts is None:
            self._failed_envelopes[item.task_id] = payload
            self._fail_attempt(item.task_id, "specialist_failed", payload)
            return
        if recorded.task.agent == AgentType.POLICY and self._outcome is not None:
            self._explanation = _accept_policy_explanation(recorded, self._outcome)
        self._journal.commit_success(recorded)
        self._settled.add(item.task_id)
        self._transition(item.task_id, TaskStatus.COMPLETED, "completed")
        if self._context is not None:
            _accept_into_context(self._context, recorded)
        self._runs[item.task_id] = recorded

    def _abandon(self, item: _InFlight, reason: str) -> None:
        item.abandoned = True
        self._inflight.pop(item.future, None)
        self._inflight_ids.discard(item.task_id)
        if reason == "workflow_timeout":
            if self._status(item.task_id) == TaskStatus.RUNNING:
                self._transition(item.task_id, TaskStatus.FAILED, reason)
                if item.task_id not in self._settled:
                    self._journal.commit_failure(self._failure_run(item.task_id, reason))
                    self._settled.add(item.task_id)
            return
        self._fail_attempt(item.task_id, reason, None)

    def _fail_attempt(
        self,
        task_id: UUID,
        reason: str,
        payload: AgentResultEnvelope | None,
    ) -> None:
        if self._status(task_id) == TaskStatus.RUNNING:
            self._transition(task_id, TaskStatus.FAILED, reason)
        task = self._one(task_id)
        if reason in _RETRYABLE and task.retry_count < self._max_retries:
            self._journal.set_retry_count(task_id, task.retry_count + 1)
            self._transition(task_id, TaskStatus.READY, "retry")
            return
        if self._status(task_id) != TaskStatus.FAILED or task_id in self._settled:
            return
        envelope = payload if payload is not None else self._failed_envelopes.get(task_id)
        self._journal.commit_failure(self._failure_run(task_id, reason, envelope))
        self._settled.add(task_id)

    def _skip_blocked(self) -> None:
        changed = True
        while changed:
            changed = False
            current = {task.id: task for task in self._journal.tasks()}
            for task in current.values():
                if task.status not in {TaskStatus.PENDING, TaskStatus.READY}:
                    continue
                deps = [current[item] for item in task.depends_on]
                if any(dep.status in UNSATISFIED for dep in deps):
                    self._transition(task.id, TaskStatus.SKIPPED, "dependency_not_completed")
                    changed = True

    def _cancel_open(self) -> None:
        for item in list(self._inflight.values()):
            item.abandoned = True
        self._inflight.clear()
        self._inflight_ids.clear()
        for task in self._journal.tasks():
            if task.status in {TaskStatus.READY, TaskStatus.RUNNING}:
                self._transition(task.id, TaskStatus.CANCELLED, "cancelled")
        self._finish(WorkflowStatus.CANCELLED, "cancelled")

    def _expire_workflow(self) -> None:
        for item in list(self._inflight.values()):
            self._abandon(item, "workflow_timeout")
        for task in self._journal.tasks():
            if task.status in {TaskStatus.PENDING, TaskStatus.READY}:
                self._transition(task.id, TaskStatus.CANCELLED, "workflow_timeout")
            elif task.status == TaskStatus.RUNNING and task.id not in self._settled:
                self._transition(task.id, TaskStatus.FAILED, "workflow_timeout")
                self._journal.commit_failure(self._failure_run(task.id, "workflow_timeout"))
                self._settled.add(task.id)
        self._finish(WorkflowStatus.FAILED, "workflow_timeout")

    def _fail_limited(self) -> None:
        reason = self._limit_reason or "tool_call_limit"
        for task in self._journal.tasks():
            if task.status in {TaskStatus.PENDING, TaskStatus.READY}:
                self._transition(task.id, TaskStatus.SKIPPED, reason)
        self._finish(WorkflowStatus.FAILED, reason)

    def _conclude(self) -> None:
        tasks = self._journal.tasks()
        if any(task.status != TaskStatus.COMPLETED for task in tasks):
            self._math = None
            self._finish(WorkflowStatus.FAILED, REASON_MISSING_EVIDENCE)
            return
        if self._context is None or AgentType.POLICY.value not in self._context.facts:
            self._math = None
            self._finish(WorkflowStatus.FAILED, REASON_MISSING_EVIDENCE)
            return
        math = self._math if self._math is not None else self._solve()
        if math is None:
            self._finish(WorkflowStatus.FAILED, REASON_MISSING_EVIDENCE)
            return
        self._math = math
        policy = self._context.facts[AgentType.POLICY.value]
        self._manager, self._finance = approvals_from_policy(policy)
        status = WorkflowStatus.COMPLETED
        if self._manager or self._finance:
            status = WorkflowStatus.WAITING_APPROVAL
        request = self._request
        assert request is not None
        supplier = self._context.facts[AgentType.SUPPLIER.value]
        outcome = self._outcome
        if outcome is None:
            self._finish(WorkflowStatus.FAILED, REASON_MISSING_EVIDENCE)
            return
        explanation = self._explanation
        if explanation is None:
            proposed = policy.get("explanation")
            explanation = accept_explanation(
                proposed if isinstance(proposed, str) else None,
                outcome,
            )
            self._explanation = explanation
        arguments = _proposal_arguments(
            request,
            supplier,
            math,
            self._manager,
            self._finance,
            outcome,
            explanation,
        )
        if arguments is not None:
            self._context.proposals.append(arguments)
        self._finish(status, math.reason)

    def _ceiling_block(self, armed: int) -> str | None:
        if self._tool_calls + armed >= self._max_tool_calls:
            return "tool_call_limit"
        if self._model_calls + armed >= self._max_model_calls:
            return "model_call_limit"
        return None

    def _past_deadline(self) -> bool:
        return self._clock() >= self._deadline_at

    def _sync_counts(self) -> None:
        self._tool_calls = _tool_calls(self._client)
        self._model_calls = _model_calls(self._client)

    def _transition(self, task_id: UUID, status: TaskStatus, reason: str) -> None:
        self._journal.transition(task_id, status, reason, datetime.now(UTC))

    def _status(self, task_id: UUID) -> TaskStatus:
        return self._one(task_id).status

    def _one(self, task_id: UUID) -> TrackedTask:
        for task in self._journal.tasks():
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def _recorded(self, task_id: UUID, payload: AgentResultEnvelope) -> SpecialistRun:
        task = self._one(task_id)
        agent_task = specialist_task(
            task_id=task_id,
            agent=AgentType(task.agent),
            tenant_id=self._tenant_id,
            parameters=self._executed_parameters(task),
            context=self._context or _empty_context(task_id),
        )
        model_id, prompt_version = _provenance(self._client, task_id)
        try:
            accepted = accept_result(agent_task, payload)
        except ContractRejection as error:
            return SpecialistRun(
                task=agent_task,
                envelope=_rejection_envelope(agent_task, error),
                status=TaskStatus.FAILED,
                decision_facts=None,
                model_id=model_id,
                prompt_version=prompt_version,
            )
        if not accepted.accepted:
            return SpecialistRun(
                task=agent_task,
                envelope=accepted.envelope,
                status=TaskStatus.FAILED,
                decision_facts=None,
                model_id=model_id,
                prompt_version=prompt_version,
            )
        return SpecialistRun(
            task=agent_task,
            envelope=accepted.envelope,
            status=TaskStatus.COMPLETED,
            decision_facts=accepted.facts,
            model_id=model_id,
            prompt_version=prompt_version,
        )

    def _executed_parameters(self, task: TrackedTask) -> dict[str, Any]:
        if task.agent == AgentType.POLICY.value and self._math is not None:
            params = self._parameters(task)
            if params is not None:
                return params
        return dict(task.plan_input)

    def _failure_run(
        self,
        task_id: UUID,
        reason: str,
        payload: AgentResultEnvelope | None = None,
    ) -> SpecialistRun:
        task = self._one(task_id)
        context = self._context or _empty_context(task_id)
        agent_task = specialist_task(
            task_id=task_id,
            agent=AgentType(task.agent),
            tenant_id=self._tenant_id,
            parameters=dict(task.plan_input),
            context=context,
        )
        envelope = payload or _failure_envelope(agent_task, reason, _failure_message(reason))
        model_id, prompt_version = _provenance(self._client, task_id)
        return SpecialistRun(
            task=agent_task,
            envelope=envelope,
            status=TaskStatus.FAILED,
            decision_facts=None,
            model_id=model_id,
            prompt_version=prompt_version,
        )

    def _finish(self, status: WorkflowStatus, reason: str | None) -> None:
        if self._closed:
            return
        self._journal.finish(status, reason, datetime.now(UTC))
        self._workflow_status = status
        self._reason = reason
        self._closed = True

    def _result(self) -> ReorderCoordination:
        self._sync_counts()
        return ReorderCoordination(
            workflow_status=self._workflow_status,
            reason=self._reason,
            math=self._math,
            manager=self._manager,
            finance=self._finance,
            runs=list(self._runs.values()),
            tool_calls=self._tool_calls,
            model_calls=self._model_calls,
            context=self._context,
            explanation=self._explanation,
        )


def _failure_message(reason: str) -> str:
    if reason in {"task_timeout", "workflow_timeout"}:
        return "The specialist did not finish before the deadline."
    if reason == "specialist_failed":
        return "The specialist failed."
    return "The task did not finish."


def _failure_envelope(task: object, code: str, message: str) -> AgentResultEnvelope:
    from opsmesh_api.agents.contracts import AgentTask

    assert isinstance(task, AgentTask)
    return AgentResultEnvelope(
        schema_version=SCHEMA_VERSION,
        agent=task.agent,
        task_id=task.task_id,
        status=AgentResultStatus.FAILED,
        evidence=[],
        facts={},
        warnings=[WarningItem(code=code, message=message)],
        confidence=Confidence.LOW,
    )


def _empty_context(workflow_id: UUID) -> WorkflowContext:
    return WorkflowContext(
        workflow_id=workflow_id,
        request={},
        facts={},
        evidence_refs=[],
        completed_tasks=[],
        open_questions=[],
        proposals=[],
    )
