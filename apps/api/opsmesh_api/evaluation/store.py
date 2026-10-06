"""Persist a suite report and read the latest one for a tenant."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.evaluation.models import EvaluationRun
from opsmesh_api.evaluation.schema import EvaluationReport, EvaluationRunRead
from opsmesh_api.services.errors import NotFoundError
from opsmesh_api.services.models import Tenant


def save_report(db: Session, report: EvaluationReport, tenant_ids: list[str]) -> None:
    """Store the same report for each tenant that can open it."""

    created_at = datetime.now(UTC)
    payload = report.model_dump(mode="json")
    for tenant_id in tenant_ids:
        db.add(
            EvaluationRun(
                id=uuid4(),
                tenant_id=tenant_id,
                suite_version=report.suite_version,
                suite=report.suite,
                created_at=created_at,
                passed=report.passed,
                failed=report.failed,
                report=payload,
            )
        )
    db.commit()


def latest_runs(
    db: Session, tenant_id: str
) -> tuple[EvaluationRunRead | None, EvaluationRunRead | None]:
    """Return the newest report and the one before it."""

    if db.get(Tenant, tenant_id) is None:
        raise NotFoundError()
    rows = db.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.tenant_id == tenant_id)
        .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
        .limit(2)
    ).all()
    if not rows:
        return None, None
    current = _read(rows[0])
    previous = _read(rows[1]) if len(rows) > 1 else None
    return current, previous


def _read(row: EvaluationRun) -> EvaluationRunRead:
    report = EvaluationReport.model_validate(row.report)
    return EvaluationRunRead(
        id=str(row.id),
        tenant_id=row.tenant_id,
        suite_version=row.suite_version,
        suite=row.suite,
        created_at=row.created_at.isoformat(),
        passed=report.passed,
        failed=report.failed,
        deterministic=report.deterministic,
        efficiency=report.efficiency,
        cases=report.cases,
        hosted_ingested=report.hosted_ingested,
    )
