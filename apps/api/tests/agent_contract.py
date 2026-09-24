"""Contract checks shared by the stub client and later specialist transports."""

from opsmesh_api.agents.contracts import (
    SCHEMA_VERSION,
    AgentClient,
    AgentResultEnvelope,
    AgentResultStatus,
    AgentTask,
    AgentType,
)
from opsmesh_api.workflows.models import Confidence


def assert_result_contract(client: AgentClient, task: AgentTask) -> AgentResultEnvelope:
    """Execute one task and require a schema-valid envelope."""

    result = AgentResultEnvelope.model_validate(client.execute(task).model_dump(mode="json"))
    assert result.schema_version == SCHEMA_VERSION
    assert result.agent == task.agent
    assert result.agent in AgentType
    assert result.task_id == task.task_id
    assert result.status in AgentResultStatus
    assert result.confidence in Confidence
    for warning in result.warnings:
        assert warning.code
        assert warning.message
    for item in result.evidence:
        assert item.type
        assert item.source
        assert item.observed_at is not None
    if result.status == AgentResultStatus.COMPLETED:
        assert result.facts
    return result
