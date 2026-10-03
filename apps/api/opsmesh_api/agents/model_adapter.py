"""Model-backed specialists. Operational numbers come from tools, not from the model."""

import json
from threading import Lock
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from opsmesh_api.agents.ceilings import (
    INPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_CEILING,
    estimate_tokens,
    fit_to_input_ceiling,
)
from opsmesh_api.agents.contracts import (
    FACT_MODELS,
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.agents.model_port import (
    ModelCompletion,
    ModelCompletionRequest,
    ModelPort,
    ModelProviderError,
)
from opsmesh_api.agents.prompts import PROMPT_VERSIONS, SYSTEM_PROMPTS
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.operations.synthesis import narrative_contradicts, stated_purchase_quantities
from opsmesh_api.tools.port import ToolPort
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import WarningItem


class ModelAgentAdapter:
    """`AgentClient` that explains tool results with a model and keeps tool facts."""

    def __init__(
        self,
        db: Session,
        model: ModelPort,
        model_id: str,
        tools: ToolPort | None = None,
    ) -> None:
        self._tools = StubAgentClient(db)
        self._tool_port = tools if tools is not None else LocalToolRegistry(db)
        self._model = model
        self._model_id = model_id
        self._provenance: dict[UUID, tuple[str, str]] = {}
        self._call_lock = Lock()
        self.model_calls = 0

    @property
    def tool_calls(self) -> int:
        return self._tools.tool_calls

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        return self._provenance.get(task_id, (None, None))

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        with self._call_lock:
            return self._execute_locked(task)

    def _execute_locked(self, task: AgentTask) -> AgentResultEnvelope:
        if task.agent not in FACT_MODELS:
            return _failed(task, "unknown_agent", "The specialist is not available.")
        grounded = self._tools.execute(task)
        if grounded.status != AgentResultStatus.COMPLETED:
            return _tool_failure(grounded)
        try:
            request = self._request(task, grounded)
        except ValidationError:
            return _failed(
                task,
                "input_token_ceiling",
                "The model input exceeded the input token ceiling.",
            )
        try:
            completion = self._model.complete(request)
        except ModelProviderError:
            self._remember(task, request)
            return _failed(task, "model_provider", "The model provider did not return a result.")
        self._remember(task, request)
        return self._interpret(task, completion, grounded)

    def _request(self, task: AgentTask, grounded: AgentResultEnvelope) -> ModelCompletionRequest:
        permitted = self._tool_port.permitted_tools(task.agent)
        system_prompt, user_prompt = fit_to_input_ceiling(
            SYSTEM_PROMPTS[task.agent],
            _user_prompt(task, grounded, permitted),
        )
        return ModelCompletionRequest(
            model_id=self._model_id,
            prompt_version=PROMPT_VERSIONS[task.agent],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_input_tokens=INPUT_TOKEN_CEILING,
            max_output_tokens=OUTPUT_TOKEN_CEILING,
            permitted_tools=permitted,
            task_id=task.task_id,
            agent=task.agent,
        )

    def _remember(self, task: AgentTask, request: ModelCompletionRequest) -> None:
        self.model_calls += 1
        self._provenance[task.task_id] = (request.model_id, request.prompt_version)

    def _interpret(
        self,
        task: AgentTask,
        completion: ModelCompletion,
        grounded: AgentResultEnvelope,
    ) -> AgentResultEnvelope:
        if (
            completion.output_tokens > OUTPUT_TOKEN_CEILING
            or estimate_tokens(completion.text) > OUTPUT_TOKEN_CEILING
        ):
            return _failed(
                task,
                "output_token_ceiling",
                "The model output exceeded the output token ceiling.",
            )
        payload = _parse_object(completion.text)
        if payload is None:
            return _failed(
                task,
                "invalid_model_output",
                "The model output is not a JSON result envelope.",
            )
        try:
            envelope = AgentResultEnvelope.model_validate(payload)
        except ValidationError:
            return _failed(
                task,
                "invalid_model_output",
                "The model output is not a JSON result envelope.",
            )
        if (
            envelope.schema_version != SCHEMA_VERSION
            or envelope.agent != task.agent
            or envelope.task_id != task.task_id
            or envelope.status != AgentResultStatus.COMPLETED
        ):
            return _failed(
                task,
                "invalid_model_output",
                "The model output is not a JSON result envelope.",
            )
        try:
            FACT_MODELS[task.agent].model_validate(envelope.facts)
        except ValidationError:
            return _failed(
                task,
                "invalid_model_output",
                "The model output is not a JSON result envelope.",
            )
        facts = _ground_facts(task.agent, grounded.facts, envelope.facts)
        return AgentResultEnvelope(
            schema_version=SCHEMA_VERSION,
            agent=task.agent,
            task_id=task.task_id,
            status=AgentResultStatus.COMPLETED,
            evidence=list(grounded.evidence),
            facts=facts,
            warnings=[],
            confidence=grounded.confidence,
        )


def _ground_facts(
    agent: AgentType,
    tool_facts: dict[str, Any],
    model_facts: dict[str, Any],
) -> dict[str, Any]:
    """Keep tool facts. A policy explanation may stay only when it matches them."""

    facts = dict(tool_facts)
    if agent != AgentType.POLICY:
        return facts
    proposed = model_facts.get("explanation")
    if not isinstance(proposed, str) or not proposed.strip():
        return facts
    quantity = facts.get("quantity")
    if isinstance(quantity, int) and any(
        item != quantity for item in stated_purchase_quantities(proposed)
    ):
        return facts
    if narrative_contradicts(
        proposed,
        requires_manager=bool(facts["requires_manager_approval"]),
        requires_finance=bool(facts["requires_finance_approval"]),
    ):
        return facts
    facts["explanation"] = proposed
    return facts


def _user_prompt(task: AgentTask, grounded: AgentResultEnvelope, permitted: tuple[str, ...]) -> str:
    payload = {
        "task_id": str(task.task_id),
        "agent": task.agent.value,
        "tenant_id": task.tenant_id,
        "parameters": task.parameters,
        "evidence_refs": [item.model_dump(mode="json") for item in task.evidence_refs],
        "tool_results": grounded.facts,
        "permitted_tools": list(permitted),
    }
    return json.dumps(payload, separators=(",", ":"))


def _parse_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _tool_failure(grounded: AgentResultEnvelope) -> AgentResultEnvelope:
    """Surface a tool error without copying invented numbers into facts."""

    codes = {item.code for item in grounded.warnings}
    status = AgentResultStatus.PARTIAL if "stock_conflict" in codes else grounded.status
    if status not in (AgentResultStatus.FAILED, AgentResultStatus.PARTIAL):
        status = AgentResultStatus.FAILED
    return grounded.model_copy(update={"status": status, "facts": {}})


def _failed(task: AgentTask, code: str, message: str) -> AgentResultEnvelope:
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
