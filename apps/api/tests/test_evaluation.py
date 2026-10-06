"""Golden-suite scores for local workflows."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.evaluation.catalog import assert_catalog_shape, load_suite, require_ids
from opsmesh_api.evaluation.execute import execute_case
from opsmesh_api.evaluation.hosted import FixtureSink, ingest_if_enabled
from opsmesh_api.evaluation.runner import run_suite
from opsmesh_api.evaluation.schema import LiveEvaluationError, MissingFixtureError
from opsmesh_api.evaluation.score import score_case
from opsmesh_api.settings import Settings
from sqlalchemy.orm import Session, sessionmaker


def test_catalog_has_one_hundred_workflows_across_every_category() -> None:
    document = load_suite()
    assert_catalog_shape(document)
    assert document.version == "1"
    assert len(document.workflows) >= 100
    integration = [item for item in document.workflows if item.gate == "integration"]
    assert integration
    assert all(item.recorded is not None for item in integration)


def test_missing_fixture_fails_the_suite() -> None:
    with pytest.raises(MissingFixtureError):
        load_suite(Path("evals/golden/does-not-exist.json"))
    with pytest.raises(MissingFixtureError) as caught:
        require_ids(load_suite(), ["gw-missing"])
    assert caught.value.missing == ["gw-missing"]


def test_smoke_suite_passes_offline(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        first = run_suite(db, "smoke", settings=Settings(_env_file=None))
        second = run_suite(db, "smoke", settings=Settings(_env_file=None))
    failed = [item for item in first.cases if not item.passed]
    assert failed == [], [(item.id, item.failures) for item in failed]
    assert {item.category for item in first.cases} == {
        item.category for item in load_suite().workflows if item.gate == "smoke"
    }
    assert all(not item.id.startswith("gw-integration-") for item in first.cases)
    assert first.efficiency.model_calls == 0
    assert first.hosted_ingested is False
    assert first.deterministic == second.deterministic
    assert first.efficiency.model_calls == second.efficiency.model_calls
    injection = next(item for item in first.cases if item.category == "prompt_injection")
    assert injection.passed
    cross_tenant = next(item for item in first.cases if item.category == "cross_tenant_access")
    assert cross_tenant.passed
    assert first.deterministic.forbidden_tools == 0
    assert first.deterministic.cross_tenant_failures == 0
    assert first.deterministic.ceilings_respected is True


def test_changed_approval_fails_the_case(session_factory: sessionmaker[Session]) -> None:
    case = next(
        item for item in load_suite().workflows if item.id == "gw-policy-approval-required-01"
    )
    with session_factory() as db:
        observation = execute_case(db, case, use_recorded=False)
    assert score_case(case, observation).passed
    tweaked = case.model_copy(deep=True)
    tweaked.expected.requires_manager_approval = False
    failed = score_case(tweaked, observation)
    assert not failed.passed
    assert "approval" in failed.failures


def test_leaked_fact_fails_the_cross_tenant_case(session_factory: sessionmaker[Session]) -> None:
    case = next(item for item in load_suite().workflows if item.id == "gw-cross-tenant-access-01")
    with session_factory() as db:
        observation = execute_case(db, case, use_recorded=False)
    assert score_case(case, observation).passed
    leaked = observation.model_copy(
        update={"evidence_text": observation.evidence_text + " andina-supply"}
    )
    assert "cross_tenant" in score_case(case, leaked).failures


def test_full_suite_uses_recorded_traces_without_a_hosted_model(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        report = run_suite(db, "full", settings=Settings(_env_file=None))
    failed = [item for item in report.cases if not item.passed]
    assert failed == [], [(item.id, item.failures) for item in failed]
    assert len(report.cases) >= 100
    recorded = [item for item in report.cases if item.id.startswith("gw-integration-")]
    assert recorded
    assert all(item.latency_ms == 123456 for item in recorded)
    assert report.efficiency.model_calls == 5 * len(recorded)


def test_live_evaluation_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSMESH_LIVE_EVAL", raising=False)
    with pytest.raises(LiveEvaluationError, match="opt-in"):
        run_suite(None, "live")  # type: ignore[arg-type]
    monkeypatch.setenv("OPSMESH_LIVE_EVAL", "1")
    with pytest.raises(LiveEvaluationError, match="configured"):
        run_suite(None, "live")  # type: ignore[arg-type]


def test_hosted_flag_does_not_replace_the_local_scorer(
    session_factory: sessionmaker[Session],
) -> None:
    constructed: list[int] = []

    def factory() -> FixtureSink:
        constructed.append(1)
        return FixtureSink()

    assert ingest_if_enabled(Settings(_env_file=None), [], client_factory=factory) is False
    assert constructed == []
    sink = FixtureSink()
    enabled = Settings(
        _env_file=None,
        aws_agentic_features_enabled=True,
        hosted_evaluation_enabled=True,
    )

    def explode() -> FixtureSink:
        raise RuntimeError("hosted evaluation endpoint unavailable")

    with session_factory() as db:
        ingested = run_suite(db, "smoke", settings=enabled, client_factory=lambda: sink)
        blocked = run_suite(db, "smoke", settings=enabled, client_factory=explode)
    assert ingested.hosted_ingested is True
    assert len(sink.workflows) == 14
    assert ingested.deterministic == blocked.deterministic
    assert blocked.hosted_ingested is False


def test_latest_evaluation_is_tenant_scoped(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    empty = client.get("/tenants/northwind/evaluations/latest")
    assert empty.status_code == 200
    assert empty.json()["run"] is None
    with session_factory() as db:
        report = run_suite(db, "smoke", settings=Settings(_env_file=None), persist=True)
    latest = client.get("/tenants/northwind/evaluations/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["run"]["passed"] == report.passed
    assert body["run"]["deterministic"]["ceilings_respected"] is True
    assert body["previous"] is None
    other = client.get("/tenants/contoso/evaluations/latest")
    assert other.status_code == 404
