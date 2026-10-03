"""Workflow records survive a new session and stay inside the tenant."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.workflows.errors import PersistenceRejected, WorkflowNotFound
from opsmesh_api.workflows.hashing import canonical_json_hash
from opsmesh_api.workflows.models import AgentResult, AgentTask, Evidence, WorkflowRun
from opsmesh_api.workflows.schemas import (
    AgentResultCreate,
    AgentTaskCreate,
    ApprovalCreate,
    EvidenceCreate,
    ProposalCreate,
    ResultEvidenceItem,
    ToolExecutionCreate,
    WarningItem,
    WorkflowRunCreate,
)
from opsmesh_api.workflows.store import WorkflowStore
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
STARTED = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


def _run(run_id: UUID | None = None) -> WorkflowRunCreate:
    return WorkflowRunCreate(
        id=run_id or uuid4(),
        workflow_type="reorder",
        requested_by="analyst@northwind.example",
        status="RUNNING",
        input={"sku": "ABC-42", "region": "mx-central"},
        started_at=STARTED,
        cost_summary={"model_calls": 0, "tool_calls": 1, "latency_ms": 12},
    )


def test_run_task_and_evidence_survive_a_new_session(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    task_id = uuid4()
    evidence_id = uuid4()
    task_input = {"sku": "ABC-42", "b": 1, "a": 2}
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        store.add_task(
            NORTHWIND,
            run.id,
            AgentTaskCreate(
                id=task_id,
                agent_type="inventory",
                status="COMPLETED",
                input=task_input,
                started_at=STARTED,
                finished_at=STARTED,
            ),
        )
        store.add_evidence(
            NORTHWIND,
            run.id,
            EvidenceCreate(
                id=evidence_id,
                type="inventory_snapshot",
                source="inventory",
                source_record_id="ABC-42:mx-central",
                observed_at=STARTED,
                checksum="abc123",
            ),
        )

    with session_factory() as session:
        loaded = WorkflowStore(session).get_run(NORTHWIND, run.id)
        assert loaded.id == run.id
        assert loaded.tenant_id == NORTHWIND
        assert loaded.status == "RUNNING"
        assert loaded.input == {"sku": "ABC-42", "region": "mx-central"}
        assert loaded.tasks[0].id == task_id
        assert loaded.tasks[0].workflow_run_id == run.id
        assert loaded.tasks[0].input_hash == canonical_json_hash(task_input)
        assert loaded.evidence[0].id == evidence_id
        assert loaded.evidence[0].workflow_run_id == run.id
        assert loaded.evidence[0].source_record_id == "ABC-42:mx-central"
        assert loaded.evidence[0].checksum == "abc123"
        assert loaded.started_at.tzinfo is not None
        assert loaded.started_at.utcoffset() is not None
        assert loaded.started_at.utcoffset().total_seconds() == 0


def test_cross_tenant_fetch_returns_no_rows(session_factory: sessionmaker[Session]) -> None:
    run = _run()
    with session_factory() as session:
        WorkflowStore(session).create_run(NORTHWIND, run)

    with session_factory() as session:
        store = WorkflowStore(session)
        with pytest.raises(WorkflowNotFound):
            store.get_run(CONTOSO, run.id)
        assert (
            session.scalar(
                select(func.count())
                .select_from(WorkflowRun)
                .where(WorkflowRun.id == run.id, WorkflowRun.tenant_id == CONTOSO)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentTask)
                .where(AgentTask.workflow_run_id == run.id, AgentTask.tenant_id == CONTOSO)
            )
            == 0
        )


def test_result_and_tool_execution_are_queryable_by_task(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    task_id = uuid4()
    arguments = {"sku": "ABC-42", "region": "mx-central"}
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        store.add_task(
            NORTHWIND,
            run.id,
            AgentTaskCreate(
                id=task_id,
                agent_type="inventory",
                status="COMPLETED",
                input=arguments,
                finished_at=STARTED,
            ),
        )
        store.add_result(
            NORTHWIND,
            run.id,
            task_id,
            AgentResultCreate(
                id=uuid4(),
                schema_version="1",
                facts={"available_units": 128},
                evidence=[
                    ResultEvidenceItem(
                        type="inventory_snapshot",
                        source="inventory",
                        source_record_id="ABC-42:mx-central",
                        observed_at=STARTED,
                    )
                ],
                warnings=[WarningItem(code="stale", message="Snapshot is older than one hour")],
                confidence="high",
                model_id="local-stub",
                prompt_version="v1",
            ),
        )
        store.add_tool_execution(
            NORTHWIND,
            run.id,
            task_id,
            ToolExecutionCreate(
                id=uuid4(),
                tool_name="inventory.snapshot",
                arguments=arguments,
                status="SUCCEEDED",
                latency_ms=18,
                retry_count=1,
                result_reference="evidence:ABC-42",
            ),
        )

    with session_factory() as session:
        result = session.scalar(select(AgentResult).where(AgentResult.task_id == task_id))
        assert result is not None
        assert result.schema_version == "1"
        assert result.facts == {"available_units": 128}
        assert result.confidence == "high"
        assert result.model_id == "local-stub"
        assert result.prompt_version == "v1"
        loaded = WorkflowStore(session).get_run(NORTHWIND, run.id)
        tool = loaded.tasks[0].tool_executions[0]
        assert tool.tool_name == "inventory.snapshot"
        assert tool.arguments_hash == canonical_json_hash(arguments)
        assert tool.status == "SUCCEEDED"
        assert tool.latency_ms == 18
        assert tool.retry_count == 1
        assert tool.result_reference == "evidence:ABC-42"


def test_two_proposals_are_distinguished_by_status(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    created = datetime(2026, 9, 23, 18, 5, tzinfo=UTC)
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        store.add_proposal(
            NORTHWIND,
            run.id,
            ProposalCreate(
                id=uuid4(),
                proposal_type="reorder",
                arguments={"quantity": 200},
                estimated_impact={"extended_cost_cents": 740000},
                risk_level="medium",
                status="SUPERSEDED",
                created_at=created,
            ),
        )
        active = store.add_proposal(
            NORTHWIND,
            run.id,
            ProposalCreate(
                id=uuid4(),
                proposal_type="reorder",
                arguments={"quantity": 250},
                estimated_impact={"extended_cost_cents": 925000},
                risk_level="low",
                status="PENDING_APPROVAL",
                created_at=created,
            ),
        )
        store.add_approval(
            NORTHWIND,
            run.id,
            active.id,
            ApprovalCreate(
                id=uuid4(),
                arguments={"quantity": 250},
                required_role="procurement_manager",
                status="PENDING",
                expires_at=datetime(2026, 9, 24, 18, 5, tzinfo=UTC),
            ),
        )
        loaded = store.get_run(NORTHWIND, run.id)
    statuses = {item.status for item in loaded.proposals}
    assert statuses == {"SUPERSEDED", "PENDING_APPROVAL"}
    pending = next(item for item in loaded.proposals if item.status == "PENDING_APPROVAL")
    assert pending.approvals[0].arguments_hash == canonical_json_hash({"quantity": 250})
    assert pending.approvals[0].required_role == "procurement_manager"


def test_a_new_proposal_supersedes_the_previous_draft(
    session_factory: sessionmaker[Session],
) -> None:
    run = _run()
    created = datetime(2026, 9, 23, 18, 5, tzinfo=UTC)
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        first = store.add_proposal(
            NORTHWIND,
            run.id,
            ProposalCreate(
                id=uuid4(),
                proposal_type="reorder",
                arguments={"quantity": 200},
                estimated_impact={"extended_cost_cents": 740000},
                risk_level="medium",
                status="DRAFT",
                created_at=created,
            ),
        )
        second = store.add_proposal(
            NORTHWIND,
            run.id,
            ProposalCreate(
                id=uuid4(),
                proposal_type="reorder",
                arguments={"quantity": 250},
                estimated_impact={"extended_cost_cents": 925000},
                risk_level="low",
                status="DRAFT",
                created_at=created,
            ),
        )
        loaded = store.get_run(NORTHWIND, run.id)
    by_id = {item.id: item.status for item in loaded.proposals}
    assert by_id[first.id] == "SUPERSEDED"
    assert by_id[second.id] == "DRAFT"
    assert sum(status != "SUPERSEDED" for status in by_id.values()) == 1


def test_self_dependent_task_is_rejected_before_insert(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = uuid4()
    with pytest.raises(ValidationError, match="depend on itself"):
        AgentTaskCreate(
            id=task_id,
            agent_type="inventory",
            depends_on=[task_id],
            status="PENDING",
            input={"sku": "ABC-42"},
        )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentTask)) == 0


def test_result_for_unknown_task_is_rejected(session_factory: sessionmaker[Session]) -> None:
    run = _run()
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        with pytest.raises(PersistenceRejected, match="does not exist"):
            store.add_result(
                NORTHWIND,
                run.id,
                uuid4(),
                AgentResultCreate(
                    id=uuid4(),
                    schema_version="1",
                    facts={},
                    evidence=[],
                    warnings=[],
                    confidence="low",
                ),
            )
        assert session.scalar(select(func.count()).select_from(AgentResult)) == 0


def test_invalid_status_is_rejected_before_insert() -> None:
    with pytest.raises(ValidationError):
        WorkflowRunCreate(
            id=uuid4(),
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status="NOPE",  # type: ignore[arg-type]
            input={},
            started_at=STARTED,
        )


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        EvidenceCreate(
            id=uuid4(),
            type="inventory_snapshot",
            source="inventory",
            source_record_id="ABC-42",
            observed_at=datetime(2026, 9, 23, 18, 0),
            checksum="abc",
        )


def test_hash_uses_sorted_keys() -> None:
    assert canonical_json_hash({"b": 1, "a": 2}) == canonical_json_hash({"a": 2, "b": 1})


def test_http_read_after_write_uses_a_new_session(
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    run = _run()
    evidence_id = uuid4()
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, run)
        store.add_evidence(
            NORTHWIND,
            run.id,
            EvidenceCreate(
                id=evidence_id,
                type="inventory_snapshot",
                source="inventory",
                source_record_id="ABC-42:mx-central",
                observed_at=STARTED,
                checksum="abc123",
            ),
        )

    response = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run.id)
    assert body["tenant_id"] == NORTHWIND
    assert body["status"] == "RUNNING"
    assert body["input"] == {"sku": "ABC-42", "region": "mx-central"}
    assert body["evidence"][0]["id"] == str(evidence_id)
    assert body["evidence"][0]["source_record_id"] == "ABC-42:mx-central"

    hidden = client.get(f"/tenants/{CONTOSO}/workflow-runs/{run.id}")
    assert hidden.status_code == 404
    assert "ABC-42" not in hidden.text


def test_http_lists_runs_newest_first_for_the_selected_tenant(
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    older = _run()
    newer = _run()
    newer = newer.model_copy(update={"started_at": STARTED + timedelta(minutes=5)})
    with session_factory() as session:
        store = WorkflowStore(session)
        store.create_run(NORTHWIND, older)
        store.create_run(NORTHWIND, newer)
        store.create_run(CONTOSO, _run())

    response = client.get(f"/tenants/{NORTHWIND}/workflow-runs")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["workflow_runs"]]
    assert ids[0] == str(newer.id)
    assert str(older.id) in ids
    assert ids.index(str(newer.id)) < ids.index(str(older.id))
    assert all(item["tenant_id"] == NORTHWIND for item in response.json()["workflow_runs"])

    other = client.get(f"/tenants/{CONTOSO}/workflow-runs")
    other_ids = [item["id"] for item in other.json()["workflow_runs"]]
    assert str(newer.id) not in other_ids
    assert str(older.id) not in other_ids


def test_http_lists_tenants(client: TestClient) -> None:
    response = client.get("/tenants")
    assert response.status_code == 200
    names = {item["tenant_id"]: item["name"] for item in response.json()["tenants"]}
    assert names["northwind"] == "Northwind"
    assert names["contoso"] == "Contoso"


def test_http_rejects_self_dependent_task(
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    run = _run()
    with session_factory() as session:
        WorkflowStore(session).create_run(NORTHWIND, run)
    task_id = uuid4()
    response = client.post(
        f"/tenants/{NORTHWIND}/workflow-runs/{run.id}/tasks",
        json={
            "id": str(task_id),
            "agent_type": "inventory",
            "depends_on": [str(task_id)],
            "status": "PENDING",
            "input": {"sku": "ABC-42"},
        },
    )
    assert response.status_code == 422
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(AgentTask)) == 0
