"""Policy retrieval cites versioned documents and does not authorize a purchase."""

import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import AgentTask, AgentType, approvals_from_policy
from opsmesh_api.agents.model_adapter import ModelAgentAdapter
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.knowledge.policy_index import (
    WARNING_CITATION_CONTRADICTS,
    WARNING_CITATION_MISSING,
    LocalPolicyIndex,
    PolicySearch,
    policies_directory,
)
from opsmesh_api.tools.handlers import policy_search
from opsmesh_api.tools.schemas import PolicySearchArgs
from sqlalchemy.orm import Session, sessionmaker
from tests.test_model_agents import MODEL_ID, ScriptedModel, _echo

NORTHWIND = "northwind"
CONTOSO = "contoso"
AS_OF = date(2026, 10, 2)
THRESHOLD = "A purchase total greater than 5000 currency units requires manager approval."


def _document(
    directory: Path,
    name: str,
    *,
    document_id: str,
    scope: str = "global",
    effective_start: str = "2026-01-01",
    effective_end: str | None = None,
    sections: dict[str, str] | None = None,
) -> Path:
    header = [
        f"id: {document_id}",
        "version: 2026.1",
        f"effective_start: {effective_start}",
    ]
    if effective_end is not None:
        header.append(f"effective_end: {effective_end}")
    header.append(f"scope: {scope}")
    body = sections or {"approval-thresholds": THRESHOLD}
    passages = "\n\n".join(f"## {title}\n\n{text}" for title, text in body.items())
    path = directory / name
    path.write_text("\n".join(header) + "\n\n" + passages + "\n", encoding="utf-8")
    return path


def _policy_task(tenant_id: str = NORTHWIND) -> AgentTask:
    return AgentTask(
        task_id=uuid4(),
        agent=AgentType.POLICY,
        tenant_id=tenant_id,
        parameters={
            "sku": "ABC-42",
            "extended_cost_cents": 925_000,
            "quantity": 250,
            "unit_cost_cents": 3700,
            "supplier_status": "approved",
            "reason": None,
            "blocked": False,
            "selected_quantile": "p90",
            "selected_demand": 345,
            "gross_need": 217,
        },
        evidence_refs=[],
    )


def _search(index: LocalPolicyIndex, tenant_id: str, query: str, limit: int | None = None):
    return index.search(PolicySearch(tenant_id=tenant_id, query=query, as_of=AS_OF, limit=limit))


def test_product_documents_live_in_the_knowledge_directory() -> None:
    directory = policies_directory()
    assert directory.parts[-2:] == ("knowledge", "policies")
    names = {path.name for path in directory.glob("*.md")}
    assert {
        "procurement-policy.md",
        "supplier-policy.md",
        "budget-approval-policy.md",
        "inventory-guidelines.md",
        "risk-policy.md",
    } <= names
    index = LocalPolicyIndex.load(directory)
    citations = _search(index, NORTHWIND, "manager approval 5000")
    assert citations
    assert all(item.effective_start <= AS_OF for item in citations)


def test_golden_manager_approval_cites_the_indexed_document(client: TestClient) -> None:
    response = client.post(
        f"/tenants/{NORTHWIND}/operations/reorder",
        json={
            "requested_by": "analyst@northwind.example",
            "sku": "ABC-42",
            "region": "mx-central",
            "service_level": 0.90,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_manager_approval"] is True
    assert body["extended_cost_cents"] == 925_000
    stored = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{body['run_id']}")
    assert stored.status_code == 200
    run = stored.json()
    policy = next(task for task in run["tasks"] if task["agent_type"] == "policy")
    result = policy["results"][0]
    assert result["facts"]["requires_manager_approval"] is True
    assert result["facts"]["blocked"] is False
    explanation = result["facts"]["explanation"]
    citations = [item["citation"] for item in result["evidence"] if item["citation"] is not None]
    cited = next(item for item in citations if item["document_id"] in explanation)
    assert cited["document_id"] in {"procurement-policy", "budget-approval-policy"}
    assert "5000" in cited["snippet"]
    assert cited["version"] in explanation
    assert cited["location"]
    assert "2026-01-01" in explanation
    digest = hashlib.sha256(
        (policies_directory() / f"{cited['document_id']}.md").read_bytes()
    ).hexdigest()
    assert cited["checksum"] == digest
    recorded = next(
        item for item in run["evidence"] if item["source_record_id"] == cited["document_id"]
    )
    assert recorded["checksum"] == digest
    assert cited["version"] in body["explanation"]
    assert result["warnings"] == []


def test_out_of_date_documents_are_not_cited(tmp_path: Path) -> None:
    _document(tmp_path, "expired.md", document_id="expired-policy", effective_end="2020-12-31")
    _document(
        tmp_path,
        "future.md",
        document_id="future-policy",
        effective_start="2099-01-01",
    )
    _document(tmp_path, "current.md", document_id="current-policy")
    found = _search(LocalPolicyIndex.load(tmp_path), NORTHWIND, "manager approval 5000")
    assert {item.document_id for item in found} == {"current-policy"}


def test_another_tenants_documents_are_absent(tmp_path: Path) -> None:
    _document(
        tmp_path,
        "northwind.md",
        document_id="northwind-only",
        scope=NORTHWIND,
        sections={"local": "Northwind ledger 5000 requires manager approval."},
    )
    _document(tmp_path, "shared.md", document_id="shared-policy")
    index = LocalPolicyIndex.load(tmp_path)
    foreign = _search(index, CONTOSO, "manager approval 5000")
    assert {item.document_id for item in foreign} == {"shared-policy"}
    home = _search(index, NORTHWIND, "northwind ledger")
    assert {item.document_id for item in home} == {"northwind-only"}


def test_policy_search_hides_another_tenants_documents(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _document(
        tmp_path,
        "northwind.md",
        document_id="northwind-only",
        scope=NORTHWIND,
        sections={"local": "Northwind ledger 5000 requires manager approval."},
    )
    index = LocalPolicyIndex.load(tmp_path)
    monkeypatch.setattr("opsmesh_api.tools.handlers.product_policy_index", lambda: index)
    with session_factory() as db:
        foreign = policy_search(
            db,
            CONTOSO,
            PolicySearchArgs(query="northwind ledger 5000"),
        )
        home = policy_search(
            db,
            NORTHWIND,
            PolicySearchArgs(query="northwind ledger 5000"),
        )
    assert foreign.data == {"citations": []}
    assert home.data is not None
    assert {item["document_id"] for item in home.data["citations"]} == {"northwind-only"}
    assert "requires_manager_approval" not in home.data
    assert "block_purchase" not in home.data


def test_retrieval_caps_passages(tmp_path: Path) -> None:
    _document(
        tmp_path,
        "long.md",
        document_id="long-policy",
        sections={f"part-{index}": f"manager approval 5000 passage {index}" for index in range(8)},
    )
    index = LocalPolicyIndex.load(tmp_path)
    assert len(_search(index, NORTHWIND, "manager approval 5000")) == 5
    assert len(_search(index, NORTHWIND, "manager approval 5000", limit=2)) == 2
    assert len(_search(index, NORTHWIND, "manager approval 5000", limit=9)) == 5


def test_changed_file_is_not_served_until_reindex(tmp_path: Path) -> None:
    path = _document(tmp_path, "live.md", document_id="live-policy")
    index = LocalPolicyIndex.load(tmp_path)
    first = _search(index, NORTHWIND, "manager approval 5000")
    assert len(first) == 1
    updated = path.read_text(encoding="utf-8") + "Added manager approval 5000.\n"
    path.write_text(updated, encoding="utf-8")
    assert _search(index, NORTHWIND, "manager approval 5000") == ()
    reloaded = _search(LocalPolicyIndex.load(tmp_path), NORTHWIND, "manager approval 5000")
    assert len(reloaded) == 1
    assert reloaded[0].checksum != first[0].checksum
    assert reloaded[0].checksum == hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_citation_keeps_the_decision(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    index = LocalPolicyIndex.load(tmp_path)
    with session_factory() as db:
        result = StubAgentClient(db, policy_search=index).execute(_policy_task())
    assert result.facts["requires_manager_approval"] is True
    assert result.facts["requires_finance_approval"] is False
    assert result.facts["blocked"] is False
    assert approvals_from_policy(result.facts) == (True, False)
    assert "decision stands without a citation" in str(result.facts["explanation"]).casefold()
    assert [item.code for item in result.warnings] == [WARNING_CITATION_MISSING]
    assert all(item.citation is None for item in result.evidence)


def test_contradictory_passage_does_not_flip_approval(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    _document(
        tmp_path,
        "waiver.md",
        document_id="waiver-policy",
        sections={
            "waiver": (
                "A purchase total greater than 5000 currency units needs no approval needed. "
                "Manager approval is not required."
            )
        },
    )
    index = LocalPolicyIndex.load(tmp_path)
    with session_factory() as db:
        result = StubAgentClient(db, policy_search=index).execute(_policy_task())
        adapter = ModelAgentAdapter(
            db,
            ScriptedModel(_echo),
            model_id=MODEL_ID,
            policy_search=index,
        )
        modeled = adapter.execute(_policy_task())
    assert result.facts["requires_manager_approval"] is True
    assert result.facts["blocked"] is False
    assert approvals_from_policy(result.facts) == (True, False)
    assert WARNING_CITATION_CONTRADICTS in {item.code for item in result.warnings}
    explanation = str(result.facts["explanation"]).casefold()
    assert "no approval needed" not in explanation
    assert "not required" not in explanation
    assert modeled.facts["requires_manager_approval"] is True
    assert modeled.facts["blocked"] is False
    assert "unnecessary" not in str(modeled.facts["explanation"]).casefold()
    assert WARNING_CITATION_CONTRADICTS in {item.code for item in modeled.warnings}
