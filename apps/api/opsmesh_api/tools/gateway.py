"""Tool gateway behind the tool port.

The live client calls Amazon Bedrock AgentCore Gateway over MCP. Tests pass a
fake transport, so the default quality gate does not need cloud credentials.
The simulator database credential is not part of the specialist configuration.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.agents.ceilings import MAX_TASK_RETRIES, TASK_TIMEOUT_SECONDS
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.agents.prompts import CONVERSION_ORDER
from opsmesh_api.settings import Settings
from opsmesh_api.tools.catalog import READ_TOOL_SPECS, WRITE_TOOL_SPECS
from opsmesh_api.tools.port import (
    ToolCall,
    ToolDefinition,
    ToolNotRegistered,
    ToolPort,
    ToolResult,
    ToolTaskNotFound,
)
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.tools.schemas import WRITE_TOOL_NAMES
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import AgentTask, ToolExecution, ToolStatus
from opsmesh_api.workflows.schemas import ToolExecutionCreate
from opsmesh_api.workflows.store import WorkflowStore

MCP_PROTOCOL_VERSION = "2026-07-28"
_CATALOG_IDENTITY = "opsmesh/tool-gateway"
_GATEWAY_ERROR = "The tool gateway did not return a result."
_RATE_LIMIT = "The gateway rate limit was reached."
_DENIED = "The agent is not allowed to call this tool."
_TENANT_DENIED = "The tenant does not match the task."
_BUSINESS_CREDENTIAL_KEYS = frozenset(
    {
        "business_credential",
        "database_password",
        "database_url",
        "password",
    }
)


class GatewayCatalogError(Exception):
    """The gateway catalog does not match the local registry."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class GatewayOutcome(StrEnum):
    """Result of one gateway call before it is stored as a tool execution."""

    SUCCEEDED = "succeeded"
    DENIED = "denied"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


class GatewayToolTarget(BaseModel):
    """One gateway tool: schema, permitted agents, and the upstream route."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    json_schema: dict[str, Any]
    permitted_agents: tuple[AgentType, ...]
    upstream_route: str = Field(min_length=1)


class OutboundAgentIdentity(BaseModel):
    """Credential the specialist presents to the gateway for one tool."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentType
    identity: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)

    @property
    def credential(self) -> str:
        return f"{self.identity}:{self.tool_name}"


class GatewaySpecialistConfig(BaseModel):
    """Specialist-facing gateway settings. Business database credentials are absent."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = ""
    protocol: str = "mcp"
    identities: dict[AgentType, str]

    def identity_for(self, agent: AgentType, tool_name: str) -> OutboundAgentIdentity:
        identity = self.identities.get(agent) or f"opsmesh/{agent.value}"
        return OutboundAgentIdentity(agent=agent, identity=identity, tool_name=tool_name)

    def process_environment(self) -> dict[str, str]:
        """Environment for a specialist process that calls the gateway."""

        pairs = ",".join(
            f"{agent.value}={identity}"
            for agent, identity in sorted(self.identities.items(), key=lambda item: item[0].value)
        )
        return {
            "TOOL_GATEWAY_ENDPOINT": self.endpoint,
            "TOOL_GATEWAY_IDENTITIES": pairs,
            "TOOL_GATEWAY_PROTOCOL": self.protocol,
        }


class GatewayToolRequest(BaseModel):
    """One outbound tool call. Observability ids match the workflow task."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any]
    agent: AgentType
    tenant_id: str
    workflow_run_id: UUID
    task_id: UUID
    identity: str
    credential: str
    endpoint: str


class GatewayToolResponse(BaseModel):
    """Parsed gateway response. A denial carries no business payload."""

    model_config = ConfigDict(extra="forbid")

    outcome: GatewayOutcome
    source: str | None = None
    observed_at: datetime | None = None
    data: dict[str, Any] | None = None
    result_reference: str | None = None
    error_message: str | None = None
    error_field: str | None = None


class GatewayTransport(Protocol):
    """Port for catalog reads and tool calls. The MCP client implements this."""

    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        """Return the gateway catalog used by the startup check."""

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        """Invoke one tool and return the parsed gateway response."""


class _GatewayHttp(Protocol):
    def request(self, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str, bytes]:
        """Send one MCP request and return status, content type, and body."""


def local_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Schemas and allowlists the gateway catalog must match."""

    return tuple(
        ToolDefinition(
            name=spec.name,
            json_schema=spec.argument_model.model_json_schema(),
            permitted_agents=tuple(sorted(spec.permitted_agents, key=lambda item: item.value)),
            read_only=spec.read_only,
        )
        for spec in sorted(READ_TOOL_SPECS, key=lambda item: item.name)
    )


def targets_from_definitions(
    definitions: tuple[ToolDefinition, ...],
) -> tuple[GatewayToolTarget, ...]:
    """Build gateway targets that match the local registry."""

    return tuple(
        GatewayToolTarget(
            name=item.name,
            json_schema=item.json_schema,
            permitted_agents=item.permitted_agents,
            upstream_route=_route_for(item.name),
        )
        for item in definitions
    )


def check_gateway_catalog(
    definitions: tuple[ToolDefinition, ...],
    targets: tuple[GatewayToolTarget, ...],
) -> None:
    """Fail closed when a gateway schema, allowlist, or route drifts."""

    local = {item.name: item for item in definitions}
    remote = {item.name: item for item in targets}
    if set(local) != set(remote):
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
    for name, item in local.items():
        if not item.read_only:
            raise GatewayCatalogError("The gateway catalog includes a tool that is not read-only.")
        target = remote[name]
        if target.json_schema != item.json_schema:
            raise GatewayCatalogError("The gateway tool schema does not match the registry.")
        if _agent_set(target.permitted_agents) != _agent_set(item.permitted_agents):
            raise GatewayCatalogError("The gateway tool allowlist does not match the registry.")
        if target.upstream_route != _route_for(name):
            raise GatewayCatalogError("The gateway tool route does not match the registry.")


def gateway_identities(raw: str, database_url: str) -> dict[AgentType, str]:
    """Map each specialist to an agent identity. Database credentials are dropped."""

    configured = _pairs(raw)
    found: dict[AgentType, str] = {}
    for agent in CONVERSION_ORDER:
        identity = configured.get(agent.value) or f"opsmesh/{agent.value}"
        if _leaks_business_credential(identity, database_url):
            identity = f"opsmesh/{agent.value}"
        found[agent] = identity
    return found


def specialist_gateway_config(settings: Settings) -> GatewaySpecialistConfig:
    """Build the specialist gateway configuration from feature settings."""

    endpoint = settings.tool_gateway_endpoint.strip()
    if _leaks_business_credential(endpoint, settings.database_url):
        endpoint = ""
    return GatewaySpecialistConfig(
        endpoint=endpoint,
        identities=gateway_identities(settings.tool_gateway_identities, settings.database_url),
    )


def build_gateway_request(
    call: ToolCall,
    config: GatewaySpecialistConfig,
    *,
    database_url: str,
) -> GatewayToolRequest:
    """Build an outbound call whose credential is scoped to the agent and tool."""

    outbound = config.identity_for(call.agent, call.tool_name)
    if _leaks_business_credential(outbound.credential, database_url):
        outbound = OutboundAgentIdentity(
            agent=call.agent,
            identity=f"opsmesh/{call.agent.value}",
            tool_name=call.tool_name,
        )
    return GatewayToolRequest(
        tool_name=call.tool_name,
        arguments=_scrub_arguments(call.arguments, database_url),
        agent=call.agent,
        tenant_id=call.tenant_id,
        workflow_run_id=call.workflow_run_id,
        task_id=call.task_id,
        identity=outbound.identity,
        credential=outbound.credential,
        endpoint=config.endpoint,
    )


def mcp_tool_call(
    request: GatewayToolRequest,
    *,
    protocol_version: str = MCP_PROTOCOL_VERSION,
) -> tuple[dict[str, str], dict[str, Any]]:
    """MCP `tools/call` headers and body for AgentCore Gateway."""

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {request.credential}",
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": "tools/call",
        "Mcp-Name": request.tool_name,
    }
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": str(request.task_id),
        "method": "tools/call",
        "params": {
            "name": request.tool_name,
            "arguments": request.arguments,
            "_meta": _meta(request, protocol_version),
        },
    }
    return headers, body


def select_tool_port(
    db: Session,
    settings: Settings,
    transport: GatewayTransport | None = None,
) -> ToolPort:
    """Use the local registry unless the gateway flag is actually on."""

    if not settings.cloud_capability(settings.cloud_tool_gateway_enabled):
        return LocalToolRegistry(db)
    config = specialist_gateway_config(settings)
    resolved = transport
    if resolved is None:
        if not config.endpoint:
            raise GatewayCatalogError("The tool gateway endpoint is not configured.")
        resolved = AgentCoreGatewayClient(
            config,
            protocol_version=settings.tool_gateway_protocol_version.strip() or MCP_PROTOCOL_VERSION,
        )
    return GatewayToolAdapter(db, config, resolved, database_url=settings.database_url)


class GatewayToolAdapter:
    """`ToolPort` that sends specialist reads through the gateway.

    Draft procurement writes always run on the local registry, so a gateway
    session does not open a connection for them.
    """

    def __init__(
        self,
        db: Session,
        config: GatewaySpecialistConfig,
        transport: GatewayTransport,
        *,
        database_url: str = "",
    ) -> None:
        self._db = db
        self._store = WorkflowStore(db)
        self._config = config
        self._transport = transport
        self._database_url = database_url
        self._definitions = {item.name: item for item in local_tool_definitions()}
        self._specs = {spec.name: spec for spec in READ_TOOL_SPECS}
        targets = transport.list_targets()
        check_gateway_catalog(tuple(self._definitions.values()), targets)
        self._targets = {item.name: item for item in targets}

    @property
    def specialist_config(self) -> GatewaySpecialistConfig:
        return self._config

    def invoke(self, call: ToolCall) -> ToolResult:
        if call.tool_name in WRITE_TOOL_NAMES:
            return LocalToolRegistry(self._db).invoke(call)
        task = self._task(call)
        if task is None:
            raise ToolTaskNotFound(call.task_id)
        call = call.model_copy(
            update={"arguments": _scrub_arguments(call.arguments, self._database_url)}
        )
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
        target = self._targets.get(call.tool_name)
        spec = self._specs.get(call.tool_name)
        if target is None or spec is None:
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
        request = build_gateway_request(call, self._config, database_url=self._database_url)
        retry_count = _next_retry(self._prior(call, arguments_hash))
        response, retry_count = self._call_with_retry(
            call,
            request,
            started,
            arguments_hash,
            retry_count,
        )
        local_denial = _local_denial(call, parsed, target)
        if local_denial is not None or response.outcome == GatewayOutcome.DENIED:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="denied",
                error_message=local_denial or response.error_message or _DENIED,
                retry_count=retry_count,
            )
        if response.outcome == GatewayOutcome.INVALID:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="invalid_arguments",
                error_message=response.error_message or "The tool arguments are invalid.",
                error_field=response.error_field,
                retry_count=retry_count,
            )
        if response.outcome == GatewayOutcome.RATE_LIMITED:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="rate_limited",
                error_message=response.error_message or _RATE_LIMIT,
                retry_count=retry_count,
            )
        if response.outcome != GatewayOutcome.SUCCEEDED or response.data is None:
            return self._fail(
                call,
                started,
                arguments_hash,
                error_code="gateway_error",
                error_message=response.error_message or _GATEWAY_ERROR,
                retry_count=retry_count,
            )
        return self._succeed(
            call,
            started,
            arguments_hash,
            response,
            target,
            retry_count=retry_count,
        )

    def permitted_tools(self, agent: AgentType) -> tuple[str, ...]:
        names = {
            target.name
            for target in self._targets.values()
            if agent in target.permitted_agents
        }
        if agent == AgentType.PROCUREMENT_EXECUTOR:
            names.update(spec.name for spec in WRITE_TOOL_SPECS)
        return tuple(sorted(names))

    def schema_for(self, name: str) -> dict[str, Any]:
        target = self._targets.get(name)
        if target is not None:
            return target.json_schema
        write = next((spec for spec in WRITE_TOOL_SPECS if spec.name == name), None)
        if write is None:
            raise ToolNotRegistered(name)
        return write.argument_model.model_json_schema()

    def _call_with_retry(
        self,
        call: ToolCall,
        request: GatewayToolRequest,
        started: float,
        arguments_hash: str,
        retry_count: int,
    ) -> tuple[GatewayToolResponse, int]:
        current = retry_count
        while True:
            try:
                response = self._transport.call_tool(request)
            except Exception:
                response = GatewayToolResponse(
                    outcome=GatewayOutcome.FAILED,
                    error_message=_GATEWAY_ERROR,
                )
            if response.outcome != GatewayOutcome.RATE_LIMITED or current >= MAX_TASK_RETRIES:
                return response, current
            self._fail(
                call,
                started,
                arguments_hash,
                error_code="rate_limited",
                error_message=response.error_message or _RATE_LIMIT,
                retry_count=current,
            )
            current += 1

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

    def _succeed(
        self,
        call: ToolCall,
        started: float,
        arguments_hash: str,
        response: GatewayToolResponse,
        target: GatewayToolTarget,
        *,
        retry_count: int,
    ) -> ToolResult:
        reference = response.result_reference or f"gateway:{target.upstream_route}:{call.tool_name}"
        stored = self._persist(
            call,
            status=ToolStatus.SUCCEEDED,
            latency_ms=_latency(started),
            retry_count=retry_count,
            result_reference=reference,
        )
        return ToolResult(
            tool_name=call.tool_name,
            status=ToolStatus.SUCCEEDED,
            arguments_hash=arguments_hash,
            retry_count=stored.retry_count,
            latency_ms=stored.latency_ms,
            execution_id=stored.id,
            source=response.source or target.upstream_route,
            observed_at=response.observed_at or datetime.now(UTC),
            data=response.data,
        )

    def _fail(
        self,
        call: ToolCall,
        started: float,
        arguments_hash: str,
        *,
        error_code: str,
        error_message: str,
        error_field: str | None = None,
        retry_count: int | None = None,
    ) -> ToolResult:
        count = retry_count
        if count is None:
            count = _next_retry(self._prior(call, arguments_hash))
        stored = self._persist(
            call,
            status=ToolStatus.FAILED,
            latency_ms=_latency(started),
            retry_count=count,
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


class AgentCoreGatewayClient:
    """Amazon Bedrock AgentCore Gateway client. HTTP calls stay in this class."""

    def __init__(
        self,
        config: GatewaySpecialistConfig,
        *,
        protocol_version: str = MCP_PROTOCOL_VERSION,
        timeout_seconds: float = TASK_TIMEOUT_SECONDS,
        http: _GatewayHttp | None = None,
    ) -> None:
        self._config = config
        self._protocol_version = protocol_version or MCP_PROTOCOL_VERSION
        self._timeout_seconds = timeout_seconds
        self._http = http

    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        targets: list[GatewayToolTarget] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            payload = self._post("tools/list", params, tool_name="", identity=_CATALOG_IDENTITY)
            page, cursor = parse_tool_page(payload)
            targets.extend(page)
            if not cursor:
                break
        else:
            raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
        return tuple(targets)

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        headers, body = mcp_tool_call(request, protocol_version=self._protocol_version)
        status, content_type, raw = self._send(request.endpoint, headers, body)
        return interpret_mcp_call(status, content_type, raw)

    def _post(
        self,
        method: str,
        params: dict[str, Any],
        *,
        tool_name: str,
        identity: str,
    ) -> dict[str, Any]:
        endpoint = self._config.endpoint
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {identity}",
            "MCP-Protocol-Version": self._protocol_version,
            "Mcp-Method": method,
        }
        if tool_name:
            headers["Mcp-Name"] = tool_name
        body = {
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": self._protocol_version,
                    "io.modelcontextprotocol/clientInfo": {"name": identity, "version": "1"},
                },
            },
        }
        status, content_type, raw = self._send(endpoint, headers, body)
        if status >= 400:
            raise GatewayCatalogError("The gateway catalog could not be read.")
        return _json_object(_body_text(content_type, raw))

    def _send(
        self,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any],
    ) -> tuple[int, str, bytes]:
        encoded = json.dumps(body).encode("utf-8")
        if self._http is not None:
            return self._http.request(url, headers, encoded)
        request = urllib.request.Request(url, data=encoded, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                return int(response.status), content_type, response.read()
        except urllib.error.HTTPError as exc:
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            return exc.code, content_type, exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GatewayCatalogError(_GATEWAY_ERROR) from exc


def parse_tool_page(payload: Mapping[str, Any]) -> tuple[list[GatewayToolTarget], str | None]:
    """Read one `tools/list` page into gateway targets."""

    result = payload.get("result")
    if not isinstance(result, dict):
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
    parsed = [_target_from_tool(tool) for tool in tools]
    cursor = result.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
    return parsed, cursor or None


def interpret_mcp_call(status: int, content_type: str, raw: bytes) -> GatewayToolResponse:
    """Map an MCP `tools/call` response onto a gateway outcome."""

    if status == 429:
        return GatewayToolResponse(outcome=GatewayOutcome.RATE_LIMITED, error_message=_RATE_LIMIT)
    if status in {401, 403}:
        return GatewayToolResponse(outcome=GatewayOutcome.DENIED, error_message=_DENIED)
    if status == 400:
        return GatewayToolResponse(
            outcome=GatewayOutcome.INVALID,
            error_message="The tool arguments are invalid.",
        )
    if status == 404:
        return GatewayToolResponse(
            outcome=GatewayOutcome.FAILED,
            error_message="The tool is not registered.",
        )
    try:
        payload = _json_object(_body_text(content_type, raw))
    except GatewayCatalogError:
        if status >= 500:
            return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)
        return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)
    error = payload.get("error")
    if isinstance(error, dict):
        return _from_rpc_error(error)
    if status >= 500:
        return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)
    result = payload.get("result")
    if not isinstance(result, dict):
        return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)
    if result.get("isError") is True:
        message = _content_text(result) or _GATEWAY_ERROR
        if _is_rate_limit(message):
            return GatewayToolResponse(
                outcome=GatewayOutcome.RATE_LIMITED,
                error_message=_RATE_LIMIT,
            )
        if _is_denial(message):
            return GatewayToolResponse(outcome=GatewayOutcome.DENIED, error_message=_DENIED)
        return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=message)
    return _from_success(result)


def _target_from_tool(tool: object) -> GatewayToolTarget:
    if not isinstance(tool, dict):
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.")
    name = tool.get("name")
    schema = tool.get("inputSchema")
    if not isinstance(name, str) or not isinstance(schema, dict):
        raise GatewayCatalogError("The gateway tool schema does not match the registry.")
    raw_meta = tool.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    raw_annotations = tool.get("annotations")
    annotations = raw_annotations if isinstance(raw_annotations, dict) else {}
    if annotations.get("readOnlyHint") is False:
        raise GatewayCatalogError("The gateway catalog includes a tool that is not read-only.")
    permitted = meta.get("opsmesh/permittedAgents", annotations.get("permittedAgents", []))
    route = meta.get("opsmesh/upstreamRoute", annotations.get("upstreamRoute", ""))
    if not isinstance(permitted, list) or not isinstance(route, str):
        raise GatewayCatalogError("The gateway tool allowlist does not match the registry.")
    try:
        agents = tuple(AgentType(item) for item in permitted)
    except ValueError as exc:
        raise GatewayCatalogError(
            "The gateway tool allowlist does not match the registry."
        ) from exc
    try:
        return GatewayToolTarget(
            name=name,
            json_schema=schema,
            permitted_agents=agents,
            upstream_route=route,
        )
    except ValidationError as exc:
        raise GatewayCatalogError("The gateway tool catalog does not match the registry.") from exc


def _from_rpc_error(error: Mapping[str, Any]) -> GatewayToolResponse:
    message = str(error.get("message") or _GATEWAY_ERROR)
    code = error.get("code")
    if code in {-32029, 429} or _is_rate_limit(message):
        return GatewayToolResponse(outcome=GatewayOutcome.RATE_LIMITED, error_message=_RATE_LIMIT)
    if code in {-32602, 400}:
        return GatewayToolResponse(outcome=GatewayOutcome.INVALID, error_message=message)
    if _is_denial(message) or code in {401, 403}:
        return GatewayToolResponse(outcome=GatewayOutcome.DENIED, error_message=_DENIED)
    return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)


def _from_success(result: Mapping[str, Any]) -> GatewayToolResponse:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _payload_response(structured)
    parsed = _content_json(result)
    if isinstance(parsed, dict):
        return _payload_response(parsed)
    return GatewayToolResponse(outcome=GatewayOutcome.FAILED, error_message=_GATEWAY_ERROR)


def _payload_response(payload: Mapping[str, Any]) -> GatewayToolResponse:
    data = payload.get("data")
    if not isinstance(data, dict):
        data = dict(payload)
    source = payload.get("source")
    observed = _parse_time(payload.get("observed_at"))
    reference = payload.get("result_reference")
    return GatewayToolResponse(
        outcome=GatewayOutcome.SUCCEEDED,
        source=source if isinstance(source, str) else None,
        observed_at=observed,
        data=data,
        result_reference=reference if isinstance(reference, str) else None,
    )


def _meta(request: GatewayToolRequest, protocol_version: str) -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": protocol_version,
        "io.modelcontextprotocol/clientInfo": {"name": request.identity, "version": "1"},
        "opsmesh/agent": request.agent.value,
        "opsmesh/tenantId": request.tenant_id,
        "opsmesh/workflowRunId": str(request.workflow_run_id),
        "opsmesh/taskId": str(request.task_id),
    }


def _local_denial(call: ToolCall, parsed: Any, target: GatewayToolTarget) -> str | None:
    if call.agent not in target.permitted_agents:
        return _DENIED
    tenant_argument = getattr(parsed, "tenant_id", None)
    if isinstance(tenant_argument, str) and tenant_argument != call.tenant_id:
        return _TENANT_DENIED
    return None


def _route_for(name: str) -> str:
    prefix, separator, _rest = name.partition(".")
    if not separator:
        return name
    return prefix


def _agent_set(agents: tuple[AgentType, ...]) -> frozenset[str]:
    return frozenset(agent.value for agent in agents)


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


def _credential_token(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.username and parsed.password:
        return f"{parsed.username}:{parsed.password}"
    return ""


def _leaks_business_credential(value: str, database_url: str) -> bool:
    if not value or not database_url:
        return False
    if database_url in value:
        return True
    token = _credential_token(database_url)
    return bool(token) and token in value


def _scrub_arguments(arguments: Mapping[str, Any], database_url: str) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in _BUSINESS_CREDENTIAL_KEYS:
            continue
        if isinstance(value, str) and _leaks_business_credential(value, database_url):
            continue
        cleaned[key] = value
    return cleaned


def _next_retry(prior: list[ToolExecution]) -> int:
    if not prior:
        return 0
    return max(row.retry_count for row in prior) + 1


def _latency(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


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


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GatewayCatalogError(_GATEWAY_ERROR) from exc
    if not isinstance(parsed, dict):
        raise GatewayCatalogError(_GATEWAY_ERROR)
    return parsed


def _body_text(content_type: str, raw: bytes) -> str:
    text = raw.decode("utf-8")
    if "text/event-stream" not in content_type:
        return text
    payloads = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    if not payloads:
        raise GatewayCatalogError(_GATEWAY_ERROR)
    return payloads[-1]


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _content_json(result: Mapping[str, Any]) -> object | None:
    text = _content_text(result)
    if not text:
        return None
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_rate_limit(message: str) -> bool:
    lowered = message.lower()
    return "rate limit" in lowered or "too many requests" in lowered or "throttl" in lowered


def _is_denial(message: str) -> bool:
    lowered = message.lower()
    return "not allowed" in lowered or "denied" in lowered or "forbidden" in lowered
