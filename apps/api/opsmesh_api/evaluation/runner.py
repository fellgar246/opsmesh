"""Run the smoke or full suite locally. Live model evaluation stays opt-in."""

import os
from collections.abc import Callable

from sqlalchemy.orm import Session

from opsmesh_api.evaluation.catalog import (
    assert_catalog_shape,
    load_smoke_ids,
    load_suite,
    require_ids,
)
from opsmesh_api.evaluation.execute import execute_case
from opsmesh_api.evaluation.hosted import HostedEvaluationPort, ingest_if_enabled
from opsmesh_api.evaluation.schema import (
    EvaluationReport,
    GoldenWorkflow,
    LiveEvaluationError,
    SuiteDocument,
)
from opsmesh_api.evaluation.score import aggregate, score_case
from opsmesh_api.evaluation.store import save_report
from opsmesh_api.settings import Settings, get_settings

SuiteName = str


def run_suite(
    db: Session,
    suite: SuiteName,
    *,
    settings: Settings | None = None,
    client_factory: Callable[[], HostedEvaluationPort] | None = None,
    document: SuiteDocument | None = None,
    persist: bool = False,
) -> EvaluationReport:
    """Score the selected workflows. The default path does not call a hosted model."""

    if suite == "live":
        _refuse_live()
    if suite not in {"smoke", "full"}:
        raise LiveEvaluationError(f"Unknown suite {suite}.")
    loaded = document if document is not None else load_suite()
    if document is None:
        assert_catalog_shape(loaded)
    cases = _select(loaded, suite)
    observations = [
        execute_case(db, case, use_recorded=suite == "full" and case.gate == "integration")
        for case in cases
    ]
    scores = [
        score_case(case, observation)
        for case, observation in zip(cases, observations, strict=True)
    ]
    report = aggregate(suite, loaded.version, cases, scores, observations)
    active = settings if settings is not None else get_settings()
    try:
        report.hosted_ingested = ingest_if_enabled(
            active,
            cases,
            client_factory=client_factory,
        )
    except Exception:
        report.hosted_ingested = False
    if persist:
        save_report(db, report, _tenants(cases))
    return report


def _select(document: SuiteDocument, suite: str) -> list[GoldenWorkflow]:
    if suite == "smoke":
        return require_ids(document, load_smoke_ids())
    return list(document.workflows)


def _tenants(cases: list[GoldenWorkflow]) -> list[str]:
    found: list[str] = []
    for case in cases:
        if case.tenant_id not in found:
            found.append(case.tenant_id)
    return found


def _refuse_live() -> None:
    if os.environ.get("OPSMESH_LIVE_EVAL") != "1":
        raise LiveEvaluationError("Live model evaluation is opt-in.")
    raise LiveEvaluationError("No live model provider is configured.")
