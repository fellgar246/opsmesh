"""Local draft purchase orders. Writes stay in the simulator and replay safely."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import AgentType
from opsmesh_api.services.models import IdempotencyRecord, PurchaseOrder, PurchaseOrderStatus
from opsmesh_api.tools.catalog import WRITE_TOOL_SPECS
from opsmesh_api.tools.gateway import (
    GatewayCatalogError,
    GatewaySpecialistConfig,
    GatewayToolAdapter,
    GatewayToolRequest,
    GatewayToolResponse,
    GatewayToolTarget,
    check_gateway_catalog,
    local_tool_definitions,
    targets_from_definitions,
)
from opsmesh_api.tools.port import ToolCall, ToolDefinition
from opsmesh_api.tools.registry import LocalToolRegistry
from opsmesh_api.tools.schemas import (
    CREATE_DRAFT_TOOL,
    SUBMIT_FOR_APPROVAL_TOOL,
    WRITE_TOOL_NAMES,
    DraftPurchaseOrderArgs,
    validated_arguments_hash,
)
from opsmesh_api.workflows.models import OperationalProposal, ProposalStatus, ToolStatus
from opsmesh_api.workflows.schemas import (
    AgentTaskCreate,
    ProposalCreate,
    WorkflowRunCreate,
)
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
SUPPLIER = "andina-supply"
REGION = "mx-central"
UNIT_COST = 3700
STARTED = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_BLOCKED_AGENTS = (
    AgentType.INVENTORY,
    AgentType.DEMAND,
    AgentType.SUPPLIER,
    AgentType.FINANCE,
    AgentType.POLICY,
    AgentType.SUPERVISOR,
)


class _SilentGateway:
    def __init__(self) -> None:
        self.calls = 0

    def list_targets(self) -> tuple[GatewayToolTarget, ...]:
        return targets_from_definitions(local_tool_definitions())

    def call_tool(self, request: GatewayToolRequest) -> GatewayToolResponse:
        self.calls += 1
        raise AssertionError(request.tool_name)


def _reorder(client: TestClient) -> dict[str, object]:
    response = client.post(
        f"/tenants/{NORTHWIND}/operations/reorder",
        json={
            "requested_by": "analyst@northwind.example",
            "sku": SKU,
            "region": REGION,
            "service_level": 0.90,
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _stored_proposal(client: TestClient, run_id: str) -> dict[str, object]:
    response = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    proposals = response.json()["proposals"]
    assert len(proposals) == 1
    proposal: dict[str, object] = proposals[0]
    return proposal


def _orders(client: TestClient, tenant_id: str = NORTHWIND) -> list[dict[str, object]]:
    response = client.get(f"/tenants/{tenant_id}/procurement/purchase-orders")
    assert response.status_code == 200
    body: list[dict[str, object]] = response.json()["purchase_orders"]
    return body


def _executor_task(db: Session, tenant_id: str, run_id: UUID) -> UUID:
    task_id = uuid4()
    WorkflowStore(db).add_task(
        tenant_id,
        run_id,
        AgentTaskCreate(
            id=task_id,
            agent_type=AgentType.PROCUREMENT_EXECUTOR.value,
            status="RUNNING",
            input={"agent": AgentType.PROCUREMENT_EXECUTOR.value},
            started_at=STARTED,
        ),
    )
    return task_id


def _run(db: Session, tenant_id: str) -> UUID:
    run_id = uuid4()
    WorkflowStore(db).create_run(
        tenant_id,
        WorkflowRunCreate(
            id=run_id,
            workflow_type="reorder",
            requested_by="analyst@northwind.example",
            status="RUNNING",
            input={"sku": SKU},
            started_at=STARTED,
        ),
    )
    return run_id


def _add_proposal(
    db: Session,
    tenant_id: str,
    run_id: UUID,
    *,
    quantity: int,
    unit_cost_cents: int,
    reason: str | None,
    status: str,
) -> UUID:
    proposal_id = uuid4()
    WorkflowStore(db).add_proposal(
        tenant_id,
        run_id,
        ProposalCreate(
            id=proposal_id,
            proposal_type="reorder",
            arguments={
                "sku": SKU,
                "supplier_id": SUPPLIER,
                "quantity": quantity,
                "unit_cost_cents": unit_cost_cents,
                "reason": reason,
            },
            estimated_impact={"extended_cost_cents": quantity * unit_cost_cents},
            risk_level="low",
            status=status,
            created_at=STARTED,
        ),
    )
    return proposal_id


def _draft_arguments(
    proposal_id: UUID,
    *,
    quantity: int = 250,
    unit_cost_cents: int = UNIT_COST,
    idempotency_key: str = "golden-draft",
) -> dict[str, object]:
    return {
        "proposal_id": str(proposal_id),
        "sku": SKU,
        "supplier_id": SUPPLIER,
        "quantity": quantity,
        "unit_cost_cents": unit_cost_cents,
        "idempotency_key": idempotency_key,
    }


def _call(
    tenant_id: str,
    run_id: UUID,
    task_id: UUID,
    agent: AgentType,
    tool_name: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        agent=agent,
        tenant_id=tenant_id,
        workflow_run_id=run_id,
        task_id=task_id,
        arguments=arguments,
    )


def _order_count(db: Session, tenant_id: str) -> int:
    count = db.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == tenant_id)
    )
    assert count is not None
    return int(count)


def test_write_tools_belong_only_to_the_procurement_executor(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        assert registry.permitted_tools(AgentType.PROCUREMENT_EXECUTOR) == tuple(
            sorted(WRITE_TOOL_NAMES)
        )
        for agent in _BLOCKED_AGENTS:
            assert set(registry.permitted_tools(agent)).isdisjoint(WRITE_TOOL_NAMES)
    assert {spec.name for spec in WRITE_TOOL_SPECS} == WRITE_TOOL_NAMES
    executor = frozenset({AgentType.PROCUREMENT_EXECUTOR})
    assert all(spec.permitted_agents == executor for spec in WRITE_TOOL_SPECS)
    assert all(not spec.read_only and spec.kind == "write" for spec in WRITE_TOOL_SPECS)
    definitions = local_tool_definitions()
    assert WRITE_TOOL_NAMES.isdisjoint({item.name for item in definitions})
    assert all(item.read_only for item in definitions)


def test_gateway_catalog_rejects_a_write_tool() -> None:
    write = ToolDefinition(
        name=CREATE_DRAFT_TOOL,
        json_schema={"type": "object"},
        permitted_agents=(AgentType.PROCUREMENT_EXECUTOR,),
        read_only=False,
    )
    with pytest.raises(GatewayCatalogError):
        check_gateway_catalog((write,), targets_from_definitions((write,)))


def test_write_path_does_not_open_a_supplier_connection() -> None:
    root = Path(__file__).parents[1] / "opsmesh_api"
    blobs = [
        (root / "services" / "procurement.py").read_text(encoding="utf-8"),
        (root / "tools" / "handlers.py").read_text(encoding="utf-8"),
    ]
    for blob in blobs:
        for token in ("urlopen", "httpx", "requests.", "socket", "https://"):
            assert token not in blob


def test_supplier_cannot_create_a_draft(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=250,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        task_id = uuid4()
        WorkflowStore(db).add_task(
            NORTHWIND,
            run_id,
            AgentTaskCreate(
                id=task_id,
                agent_type=AgentType.SUPPLIER.value,
                status="RUNNING",
                input={"sku": SKU},
                started_at=STARTED,
            ),
        )
        registry = LocalToolRegistry(db)
        before = _order_count(db, NORTHWIND)
        denied = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.SUPPLIER,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id),
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        assert denied.data is None
        assert registry.handler_calls == 0
        assert _order_count(db, NORTHWIND) == before


def test_golden_proposal_creates_one_replayable_draft(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    body = _reorder(client)
    run_id = str(body["run_id"])
    proposal = _stored_proposal(client, run_id)
    arguments = proposal["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["sku"] == SKU
    assert arguments["quantity"] == 250
    assert arguments["supplier_id"] == SUPPLIER
    assert arguments["unit_cost_cents"] == UNIT_COST
    proposal_id = UUID(str(proposal["id"]))
    draft_arguments = _draft_arguments(proposal_id)
    with session_factory() as db:
        task_id = _executor_task(db, NORTHWIND, UUID(run_id))
        registry = LocalToolRegistry(db)
        first = registry.invoke(
            _call(
                NORTHWIND,
                UUID(run_id),
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                draft_arguments,
            )
        )
        second = registry.invoke(
            _call(
                NORTHWIND,
                UUID(run_id),
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                draft_arguments,
            )
        )
        assert first.status == ToolStatus.SUCCEEDED
        assert first.data is not None
        assert second.data is not None
        assert first.data["purchase_order_id"] == second.data["purchase_order_id"]
        assert first.data["status"] == "draft"
        assert first.data["quantity"] == 250
        assert first.data["sku"] == SKU
        assert first.data["supplier_id"] == SUPPLIER
        assert first.data["unit_cost_cents"] == UNIT_COST
        assert first.arguments_hash == validated_arguments_hash(
            DraftPurchaseOrderArgs.model_validate(draft_arguments)
        )
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "golden-draft")
        )
        assert record is not None
        assert record.arguments_hash == first.arguments_hash
        assert record.result_reference == first.data["purchase_order_id"]
        assert record.tool_name == CREATE_DRAFT_TOOL
    listed = _orders(client)
    drafts = [order for order in listed if order["status"] == "draft"]
    assert len(drafts) == 1
    assert drafts[0]["purchase_order_id"] == first.data["purchase_order_id"]
    fetched = client.get(
        f"/tenants/{NORTHWIND}/procurement/purchase-orders/{first.data['purchase_order_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "draft"
    assert fetched.json()["quantity"] == 250


def test_same_key_with_a_different_quantity_keeps_the_original_draft(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    body = _reorder(client)
    run_id = UUID(str(body["run_id"]))
    proposal = _stored_proposal(client, str(run_id))
    proposal_id = UUID(str(proposal["id"]))
    with session_factory() as db:
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        created = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, idempotency_key="replay-key"),
            )
        )
        assert created.status == ToolStatus.SUCCEEDED
        assert created.data is not None
        order_id = str(created.data["purchase_order_id"])
        conflict = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, quantity=200, idempotency_key="replay-key"),
            )
        )
        assert conflict.status == ToolStatus.FAILED
        assert conflict.error_code == "idempotency_conflict"
        assert conflict.data is None
        stored = db.scalar(select(PurchaseOrder).where(PurchaseOrder.purchase_order_id == order_id))
        assert stored is not None
        assert stored.quantity == 250
        assert stored.status == PurchaseOrderStatus.DRAFT.value
        drafts = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == NORTHWIND,
                PurchaseOrder.status == PurchaseOrderStatus.DRAFT.value,
            )
        ).all()
        assert len(drafts) == 1


def test_missing_and_infeasible_proposals_do_not_write(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        infeasible_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=0,
            unit_cost_cents=UNIT_COST,
            reason="insufficient_budget",
            status="DRAFT",
        )
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        before = _order_count(db, NORTHWIND)
        missing = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(uuid4(), idempotency_key="missing-proposal"),
            )
        )
        mismatched = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(infeasible_id, quantity=250, idempotency_key="infeasible"),
            )
        )
        assert missing.status == ToolStatus.FAILED
        assert missing.error_code == "denied"
        assert mismatched.status == ToolStatus.FAILED
        assert mismatched.error_code == "denied"
        assert _order_count(db, NORTHWIND) == before


def test_arguments_that_differ_from_the_proposal_do_not_write(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=250,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        before = _order_count(db, NORTHWIND)
        denied = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, quantity=200, idempotency_key="wrong-quantity"),
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        assert denied.error_message == "The tool arguments do not match the proposal."
        assert _order_count(db, NORTHWIND) == before


def test_a_stored_order_is_returned_when_the_idempotency_record_is_missing(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=250,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        arguments = _draft_arguments(proposal_id, idempotency_key="partial-draft")
        digest = validated_arguments_hash(DraftPurchaseOrderArgs.model_validate(arguments))
        order_id = "po-partial-draft"
        db.add(
            PurchaseOrder(
                purchase_order_id=order_id,
                tenant_id=NORTHWIND,
                supplier_id=SUPPLIER,
                sku=SKU,
                quantity=250,
                status=PurchaseOrderStatus.DRAFT.value,
                expected_receipt_on=date(2026, 10, 8),
                proposal_id=proposal_id,
                unit_cost_cents=UNIT_COST,
                idempotency_key="partial-draft",
                arguments_hash=digest,
            )
        )
        db.commit()
        task_id = _executor_task(db, NORTHWIND, run_id)
        result = LocalToolRegistry(db).invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                arguments,
            )
        )
        assert result.status == ToolStatus.SUCCEEDED
        assert result.data is not None
        assert result.data["purchase_order_id"] == order_id
        drafts = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == NORTHWIND,
                PurchaseOrder.idempotency_key == "partial-draft",
            )
        ).all()
        assert len(drafts) == 1
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "partial-draft")
        )
        assert record is not None
        assert record.arguments_hash == digest
        assert record.result_reference == order_id


def test_submit_does_not_approve_the_golden_draft_without_a_decision(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    body = _reorder(client)
    run_id = UUID(str(body["run_id"]))
    proposal = _stored_proposal(client, str(run_id))
    proposal_id = UUID(str(proposal["id"]))
    assert proposal["status"] == "PENDING_APPROVAL"
    with session_factory() as db:
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        created = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, idempotency_key="submit-draft"),
            )
        )
        assert created.data is not None
        order_id = str(created.data["purchase_order_id"])
        denied = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                SUBMIT_FOR_APPROVAL_TOOL,
                {
                    "purchase_order_id": order_id,
                    "idempotency_key": "submit-key",
                },
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "approval_required"
        stored_order = db.get(PurchaseOrder, order_id)
        assert stored_order is not None
        assert stored_order.status == PurchaseOrderStatus.DRAFT.value
        stored = db.get(OperationalProposal, proposal_id)
        assert stored is not None
        assert stored.status == ProposalStatus.PENDING_APPROVAL.value
        assert stored.status != ProposalStatus.APPROVED.value
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "submit-key")
        )
        assert record is None


def test_submit_leaves_a_small_proposal_unapproved(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=10,
            unit_cost_cents=100,
            reason=None,
            status="DRAFT",
        )
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        created = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(
                    proposal_id,
                    quantity=10,
                    unit_cost_cents=100,
                    idempotency_key="small-draft",
                ),
            )
        )
        assert created.data is not None
        submitted = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                SUBMIT_FOR_APPROVAL_TOOL,
                {
                    "purchase_order_id": created.data["purchase_order_id"],
                    "idempotency_key": "small-submit",
                },
            )
        )
        assert submitted.status == ToolStatus.SUCCEEDED
        assert submitted.data is not None
        assert submitted.data["status"] == "waiting_approval"
        stored = db.get(OperationalProposal, proposal_id)
        assert stored is not None
        assert stored.status == ProposalStatus.DRAFT.value


def test_a_draft_in_another_tenant_is_not_submitted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=250,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        task_id = _executor_task(db, NORTHWIND, run_id)
        registry = LocalToolRegistry(db)
        created = registry.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, idempotency_key="tenant-draft"),
            )
        )
        assert created.data is not None
        other_run = _run(db, CONTOSO)
        other_task = _executor_task(db, CONTOSO, other_run)
        denied = registry.invoke(
            _call(
                CONTOSO,
                other_run,
                other_task,
                AgentType.PROCUREMENT_EXECUTOR,
                SUBMIT_FOR_APPROVAL_TOOL,
                {
                    "purchase_order_id": created.data["purchase_order_id"],
                    "idempotency_key": "tenant-submit",
                },
            )
        )
        assert denied.status == ToolStatus.FAILED
        assert denied.error_code == "denied"
        original = db.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.purchase_order_id == created.data["purchase_order_id"]
            )
        )
        assert original is not None
        assert original.status == PurchaseOrderStatus.DRAFT.value


def test_gateway_sessions_create_the_draft_locally(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        run_id = _run(db, NORTHWIND)
        proposal_id = _add_proposal(
            db,
            NORTHWIND,
            run_id,
            quantity=250,
            unit_cost_cents=UNIT_COST,
            reason=None,
            status="PENDING_APPROVAL",
        )
        task_id = _executor_task(db, NORTHWIND, run_id)
        transport = _SilentGateway()
        adapter = GatewayToolAdapter(
            db,
            GatewaySpecialistConfig(endpoint="https://gateway.example/mcp", identities={}),
            transport,
        )
        result = adapter.invoke(
            _call(
                NORTHWIND,
                run_id,
                task_id,
                AgentType.PROCUREMENT_EXECUTOR,
                CREATE_DRAFT_TOOL,
                _draft_arguments(proposal_id, idempotency_key="gateway-draft"),
            )
        )
        assert transport.calls == 0
        assert result.status == ToolStatus.SUCCEEDED
        assert result.data is not None
        assert result.data["status"] == "draft"
        assert set(adapter.permitted_tools(AgentType.PROCUREMENT_EXECUTOR)) == WRITE_TOOL_NAMES
        assert CREATE_DRAFT_TOOL not in adapter.permitted_tools(AgentType.SUPPLIER)
