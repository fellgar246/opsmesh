"""Local tool registry. Invocations are validated, authorized, and audited."""

from collections.abc import Sequence
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.services.errors import NotFoundError, StockConflictError
from opsmesh_api.tools.catalog import READ_TOOL_SPECS, ToolSpec
from opsmesh_api.tools.port import (
    HandlerResult,
    ToolCall,
    ToolDefinition,
    ToolNotRegistered,
    ToolRegistrationError,
    ToolResult,
    ToolTaskNotFound,
)
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import AgentTask, ToolExecution, ToolStatus
from opsmesh_api.workflows.schemas import ToolExecutionCreate
from opsmesh_api.workflows.store import WorkflowStore

_FORBIDDEN_KINDS = frozenset({"url", "sql", "shell"})
_READ_KIND = "read"


class LocalToolRegistry:
    """In-process registry. Specialists depend on `ToolPort`, not on this class."""

    def __init__(self, db: Session, specs: Sequence[ToolSpec] | None = None) -> None:
        self._db = db
        self._store = WorkflowStore(db)
        self._specs: dict[str, ToolSpec] = {}
        self.handler_calls = 0
        self._cache: dict[tuple[UUID, str, str], ToolResult] = {}
        for spec in READ_TOOL_SPECS if specs is None else specs:
            self._add(spec)

    def invoke(self, call: ToolCall) -> ToolResult:
        """Validate and authorize one call, then persist the invocation."""

        task = self._task(call)
        if task is None:
            raise ToolTaskNotFound(call.task_id)
        started = perf_counter()
        arguments_hash = canonical_json_hash(call.arguments)
        if task.agent_type != call.agent.value:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="denied",
                error_message="The agent does not match the task.",
            )
        spec = self._specs.get(call.tool_name)
        if spec is None:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="unknown_tool",
                error_message="The tool is not registered.",
            )
        try:
            parsed = spec.argument_model.model_validate(call.arguments)
        except ValidationError as exc:
            field, message = _validation_message(exc)
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="invalid_arguments",
                error_message=message,
                error_field=field,
            )
        if call.agent not in spec.permitted_agents:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="denied",
                error_message="The agent is not allowed to call this tool.",
            )
        tenant_argument = getattr(parsed, "tenant_id", None)
        if isinstance(tenant_argument, str) and tenant_argument != call.tenant_id:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="denied",
                error_message="The tenant does not match the task.",
            )
        prior = self._prior(call, arguments_hash)
        succeeded = next((row for row in prior if row.status == ToolStatus.SUCCEEDED), None)
        if succeeded is not None:
            return self._replay_success(call, spec, parsed, arguments_hash, succeeded, prior)
        try:
            payload = self._execute(spec, call, parsed)
        except NotFoundError:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="not_found",
                error_message="The requested record was not found.",
            )
        except StockConflictError:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="stock_conflict",
                error_message="Reserved units exceed on-hand units.",
            )
        return self._succeed(call, started, arguments_hash, payload, prior)

    def permitted_tools(self, agent: AgentType) -> tuple[str, ...]:
        return tuple(
            sorted(spec.name for spec in self._specs.values() if agent in spec.permitted_agents)
        )

    def schema_for(self, name: str) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolNotRegistered(name)
        return spec.argument_model.model_json_schema()

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=spec.name,
                json_schema=spec.argument_model.model_json_schema(),
                permitted_agents=tuple(sorted(spec.permitted_agents, key=lambda item: item.value)),
                read_only=spec.read_only,
            )
            for spec in sorted(self._specs.values(), key=lambda item: item.name)
        )

    def _add(self, spec: ToolSpec) -> None:
        if spec.kind in _FORBIDDEN_KINDS or spec.kind != _READ_KIND or not spec.read_only:
            raise ToolRegistrationError("Only read-only tools can be registered.")
        if not spec.name or spec.name in self._specs:
            raise ToolRegistrationError("Tool names must be unique and stable.")
        self._specs[spec.name] = spec

    def _task(self, call: ToolCall) -> AgentTask | None:
        return self._db.scalar(
            select(AgentTask).where(
                AgentTask.id == call.task_id,
                AgentTask.tenant_id == call.tenant_id,
                AgentTask.workflow_run_id == call.workflow_run_id,
            )
        )

    def _prior(self, call: ToolCall, arguments_hash: str) -> list[ToolExecution]:
        return list(
            self._db.scalars(
                select(ToolExecution)
                .where(
                    ToolExecution.tenant_id == call.tenant_id,
                    ToolExecution.task_id == call.task_id,
                    ToolExecution.tool_name == call.tool_name,
                    ToolExecution.arguments_hash == arguments_hash,
                )
                .order_by(ToolExecution.retry_count)
            ).all()
        )

    def _execute(self, spec: ToolSpec, call: ToolCall, parsed: BaseModel) -> HandlerResult:
        self.handler_calls += 1
        return spec.handler(self._db, call.tenant_id, parsed)

    def _replay_success(
        self,
        call: ToolCall,
        spec: ToolSpec,
        parsed: BaseModel,
        arguments_hash: str,
        succeeded: ToolExecution,
        prior: list[ToolExecution],
    ) -> ToolResult:
        cached = self._cache.get((call.task_id, call.tool_name, arguments_hash))
        if cached is not None:
            return cached.model_copy()
        started = perf_counter()
        try:
            payload = self._execute(spec, call, parsed)
        except NotFoundError:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="not_found",
                error_message="The requested record was not found.",
                prior=prior,
            )
        except StockConflictError:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="stock_conflict",
                error_message="Reserved units exceed on-hand units.",
                prior=prior,
            )
        result = _result(
            call,
            status=ToolStatus.SUCCEEDED,
            arguments_hash=arguments_hash,
            retry_count=succeeded.retry_count,
            latency_ms=succeeded.latency_ms,
            execution_id=succeeded.id,
            payload=payload,
        )
        self._cache[(call.task_id, call.tool_name, arguments_hash)] = result
        return result.model_copy()

    def _succeed(
        self,
        call: ToolCall,
        started: float,
        arguments_hash: str,
        payload: HandlerResult,
        prior: list[ToolExecution],
    ) -> ToolResult:
        stored = self._persist(
            call,
            status=ToolStatus.SUCCEEDED,
            latency_ms=_latency(started),
            retry_count=_next_retry(prior),
            result_reference=payload.result_reference,
        )
        result = _result(
            call,
            status=ToolStatus.SUCCEEDED,
            arguments_hash=arguments_hash,
            retry_count=stored.retry_count,
            latency_ms=stored.latency_ms,
            execution_id=stored.id,
            payload=payload,
        )
        self._cache[(call.task_id, call.tool_name, arguments_hash)] = result
        return result.model_copy()

    def _fail(
        self,
        call: ToolCall,
        started: float,
        arguments_hash: str,
        *,
        error_code: str,
        error_message: str,
        error_field: str | None = None,
        prior: list[ToolExecution] | None = None,
    ) -> ToolResult:
        rows = self._prior(call, arguments_hash) if prior is None else prior
        stored = self._persist(
            call,
            status=ToolStatus.FAILED,
            latency_ms=_latency(started),
            retry_count=_next_retry(rows),
            result_reference=None,
        )
        return ToolResult(
            tool_name=call.tool_name,
            status=ToolStatus.FAILED,
            arguments_hash=arguments_hash,
            retry_count=stored.retry_count,
            latency_ms=stored.latency_ms,
            execution_id=stored.id,
            error_code=error_code,
            error_message=error_message,
            error_field=error_field,
        )

    def _persist(
        self,
        call: ToolCall,
        *,
        status: ToolStatus,
        latency_ms: int,
        retry_count: int,
        result_reference: str | None,
    ) -> ToolExecution:
        execution_id = uuid4()
        reference = None if result_reference is None else result_reference[:200]
        self._store.add_tool_execution(
            call.tenant_id,
            call.workflow_run_id,
            call.task_id,
            ToolExecutionCreate(
                id=execution_id,
                tool_name=call.tool_name,
                arguments=call.arguments,
                status=status,
                latency_ms=latency_ms,
                retry_count=retry_count,
                result_reference=reference,
            ),
        )
        row = self._db.get(ToolExecution, execution_id)
        if row is None:
            raise ToolTaskNotFound(call.task_id)
        return row


def _next_retry(prior: list[ToolExecution]) -> int:
    if not prior:
        return 0
    return max(row.retry_count for row in prior) + 1


def _latency(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _result(
    call: ToolCall,
    *,
    status: ToolStatus,
    arguments_hash: str,
    retry_count: int,
    latency_ms: int,
    execution_id: UUID,
    payload: HandlerResult,
) -> ToolResult:
    return ToolResult(
        tool_name=call.tool_name,
        status=status,
        arguments_hash=arguments_hash,
        retry_count=retry_count,
        latency_ms=latency_ms,
        execution_id=execution_id,
        source=payload.source,
        observed_at=payload.observed_at,
        data=payload.data,
    )


def _validation_message(exc: ValidationError) -> tuple[str, str]:
    missing: list[str] = []
    invalid: list[str] = []
    for error in exc.errors():
        location = error.get("loc") or ()
        name = str(location[0]) if location else "arguments"
        if error.get("type") == "missing":
            missing.append(name)
        else:
            invalid.append(name)
    if missing:
        names = ", ".join(sorted(set(missing)))
        return missing[0], f"Missing required field: {names}"
    names = ", ".join(sorted(set(invalid))) or "arguments"
    field = invalid[0] if invalid else "arguments"
    return field, f"Invalid field: {names}"
