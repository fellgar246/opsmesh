"""Remote specialist runtime behind the agent client port.

The live client calls Amazon Bedrock AgentCore Runtime. Tests pass a fake
transport, so the default quality gate does not need cloud credentials.
"""

import importlib
import json
from collections.abc import Mapping
from enum import StrEnum
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opsmesh_api.agents.ceilings import TASK_TIMEOUT_SECONDS
from opsmesh_api.agents.contracts import (
    FACT_MODELS,
    SCHEMA_VERSION,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.workflows.models import Confidence
from opsmesh_api.workflows.schemas import WarningItem

_TIMEOUT_MESSAGE = "The specialist did not finish before the deadline."
_TRANSPORT_MESSAGE = "The remote runtime did not return a result."
_INVALID_MESSAGE = "The remote result envelope is invalid."
_CREDENTIAL_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "credential",
        "requested_by",
        "runtime_user_id",
        "runtimeUserId",
        "user_credential",
    }
)


class AgentTransport(StrEnum):
    """Transport stored on an endpoint. Local code does not call a runtime."""

    LOCAL = "local"
    REMOTE = "remote"


class AgentEndpoint(BaseModel):
    """Where one specialist runs."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentType
    transport: AgentTransport
    endpoint_id: str = ""


class RuntimeInvocation(BaseModel):
    """One outbound runtime call. The identity is the specialist, not an end user."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentType
    endpoint_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    qualifier: str = Field(min_length=1)
    session_id: str = Field(min_length=33)
    timeout_seconds: float
    payload: dict[str, Any]


class RuntimeTransportError(Exception):
    """The runtime call failed before a result envelope was available."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AgentRuntimeTransport(Protocol):
    """Port for one runtime invocation. The cloud SDK implements this."""

    def invoke(self, request: RuntimeInvocation) -> dict[str, Any]:
        """Send the task and return the runtime JSON body."""


class _InvokeClient(Protocol):
    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        """Call the provider invoke operation."""


def selected_remote_agents(raw: str, order: tuple[AgentType, ...]) -> tuple[AgentType, ...]:
    """Keep the conversion order. A blank list selects no remote specialist."""

    if not raw.strip():
        return ()
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(agent for agent in order if agent.value in requested)


def agent_identities(raw: str, order: tuple[AgentType, ...]) -> dict[AgentType, str]:
    """Map each specialist to its agent identity. Missing entries use a stable id."""

    configured = _pairs(raw)
    return {agent: configured.get(agent.value) or f"opsmesh/{agent.value}" for agent in order}


def remote_endpoints(selected: tuple[AgentType, ...], raw: str) -> dict[AgentType, AgentEndpoint]:
    """Build a remote endpoint for each selected specialist."""

    configured = _pairs(raw)
    return {
        agent: AgentEndpoint(
            agent=agent,
            transport=AgentTransport.REMOTE,
            endpoint_id=configured.get(agent.value, ""),
        )
        for agent in selected
    }


def session_id_for(task_id: UUID) -> str:
    """Session id long enough for the runtime and unique to the task."""

    return f"opsmesh-{task_id}"


def task_payload(task: AgentTask, caller_credential: str | None) -> dict[str, Any]:
    """Copy the task and drop end-user credential material."""

    payload = task.model_dump(mode="json")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return payload
    cleaned: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in _CREDENTIAL_KEYS:
            continue
        if caller_credential is not None and value == caller_credential:
            continue
        cleaned[key] = value
    payload["parameters"] = cleaned
    return payload


def runtime_invoke_arguments(request: RuntimeInvocation) -> dict[str, Any]:
    """Arguments for `invoke_agent_runtime`. The end-user id is not included."""

    body = {"agent_identity": request.agent_identity, "task": request.payload}
    return {
        "agentRuntimeArn": request.endpoint_id,
        "qualifier": request.qualifier,
        "runtimeSessionId": request.session_id,
        "contentType": "application/json",
        "accept": "application/json",
        "payload": json.dumps(body).encode(),
    }


def read_runtime_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Read a JSON object from a runtime response body."""

    status = response.get("statusCode")
    if isinstance(status, int) and status >= 400:
        raise RuntimeTransportError(_TRANSPORT_MESSAGE)
    content_type = str(response.get("contentType") or "")
    text = _response_text(response.get("response"))
    if "text/event-stream" in content_type:
        text = _sse_payload(text)
    return _json_object(text)


class RemoteAgentClient:
    """`AgentClient` that maps a runtime response onto the result envelope."""

    def __init__(
        self,
        endpoints: Mapping[AgentType, AgentEndpoint],
        transport: AgentRuntimeTransport,
        identities: Mapping[AgentType, str],
        *,
        qualifier: str = "DEFAULT",
        timeout_seconds: float = TASK_TIMEOUT_SECONDS,
        caller_credential: str | None = None,
    ) -> None:
        self._endpoints = dict(endpoints)
        self._transport = transport
        self._identities = dict(identities)
        self._qualifier = qualifier.strip() or "DEFAULT"
        self._timeout_seconds = timeout_seconds
        self._caller_credential = caller_credential
        self.tool_calls = 0
        self.model_calls = 0

    def execute(self, task: AgentTask) -> AgentResultEnvelope:
        if task.agent not in FACT_MODELS:
            return _failed(task, "unknown_agent", "The specialist is not available.")
        endpoint = self._endpoints.get(task.agent)
        if endpoint is None or endpoint.transport != AgentTransport.REMOTE:
            return _failed(task, "transport", _TRANSPORT_MESSAGE)
        if not endpoint.endpoint_id:
            return _failed(task, "transport", "The specialist has no runtime endpoint.")
        if self._timeout_seconds <= 0:
            return _failed(task, "timeout", _TIMEOUT_MESSAGE)
        identity = self._identities.get(task.agent) or f"opsmesh/{task.agent.value}"
        if self._caller_credential is not None and identity == self._caller_credential:
            identity = f"opsmesh/{task.agent.value}"
        request = RuntimeInvocation(
            agent=task.agent,
            endpoint_id=endpoint.endpoint_id,
            agent_identity=identity,
            qualifier=self._qualifier,
            session_id=session_id_for(task.task_id),
            timeout_seconds=self._timeout_seconds,
            payload=task_payload(task, self._caller_credential),
        )
        started = perf_counter()
        try:
            body = self._transport.invoke(request)
        except TimeoutError:
            return _failed(task, "timeout", _TIMEOUT_MESSAGE)
        except Exception:
            return _failed(task, "transport", _TRANSPORT_MESSAGE)
        if perf_counter() - started > self._timeout_seconds:
            return _failed(task, "timeout", _TIMEOUT_MESSAGE)
        return _envelope_from_body(task, body)

    def provenance_for(self, task_id: UUID) -> tuple[str | None, str | None]:
        del task_id
        return None, None


class AgentCoreRuntimeClient:
    """Amazon Bedrock AgentCore Runtime client. SDK imports stay in this class."""

    def __init__(
        self,
        region: str,
        client: _InvokeClient | None = None,
        timeout_seconds: float = TASK_TIMEOUT_SECONDS,
    ) -> None:
        self._region = region
        self._client = client
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: RuntimeInvocation) -> dict[str, Any]:
        arguments = runtime_invoke_arguments(request)
        try:
            response = self._sdk().invoke_agent_runtime(**arguments)
        except TimeoutError:
            raise
        except Exception as exc:
            if _is_timeout(exc):
                raise TimeoutError(_TIMEOUT_MESSAGE) from exc
            raise RuntimeTransportError(_TRANSPORT_MESSAGE) from exc
        return read_runtime_response(response)

    def _sdk(self) -> _InvokeClient:
        if self._client is not None:
            return self._client
        try:
            boto3 = importlib.import_module("boto3")
            config_module = importlib.import_module("botocore.config")
        except ImportError as exc:
            raise RuntimeTransportError("The agent runtime client is not installed.") from exc
        factory = getattr(boto3, "client", None)
        config_type = getattr(config_module, "Config", None)
        if not callable(factory) or not callable(config_type):
            raise RuntimeTransportError("The agent runtime client is not installed.")
        config = config_type(
            read_timeout=self._timeout_seconds,
            connect_timeout=self._timeout_seconds,
            retries={"max_attempts": 1},
        )
        return cast(
            _InvokeClient,
            factory("bedrock-agentcore", region_name=self._region, config=config),
        )


def _pairs(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in raw.split(","):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        agent = key.strip()
        item = value.strip()
        if agent and item:
            found[agent] = item
    return found


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


def _envelope_from_body(task: AgentTask, body: dict[str, Any]) -> AgentResultEnvelope:
    try:
        envelope = AgentResultEnvelope.model_validate(body)
    except ValidationError:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    if envelope.schema_version != SCHEMA_VERSION or envelope.agent != task.agent:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    if envelope.task_id != task.task_id:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    if envelope.status != AgentResultStatus.COMPLETED:
        return envelope
    model = FACT_MODELS.get(envelope.agent)
    if model is None:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    unknown = sorted(key for key in envelope.facts if key not in model.model_fields)
    if unknown:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    try:
        model.model_validate(envelope.facts)
    except ValidationError:
        return _failed(task, "invalid_envelope", _INVALID_MESSAGE)
    return envelope


def _response_text(body: object) -> str:
    if isinstance(body, bytes):
        return body.decode("utf-8")
    if isinstance(body, str):
        return body
    read = getattr(body, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, bytes):
            return data.decode("utf-8")
        if isinstance(data, str):
            return data
    if isinstance(body, list):
        parts: list[str] = []
        for chunk in body:
            if isinstance(chunk, bytes):
                parts.append(chunk.decode("utf-8"))
            elif isinstance(chunk, str):
                parts.append(chunk)
        if parts:
            return "".join(parts)
    raise RuntimeTransportError(_TRANSPORT_MESSAGE)


def _sse_payload(text: str) -> str:
    payloads = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    if not payloads:
        raise RuntimeTransportError(_TRANSPORT_MESSAGE)
    return payloads[-1]


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeTransportError(_TRANSPORT_MESSAGE) from exc
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise RuntimeTransportError(_TRANSPORT_MESSAGE) from exc
    if not isinstance(parsed, dict):
        raise RuntimeTransportError(_TRANSPORT_MESSAGE)
    return parsed


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return type(exc).__name__ in {
        "ConnectTimeout",
        "ConnectTimeoutError",
        "ReadTimeout",
        "ReadTimeoutError",
    }
