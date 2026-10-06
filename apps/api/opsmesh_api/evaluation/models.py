"""Stored evaluation runs. The console reads the latest one for the tenant."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh_api.db import Base
from opsmesh_api.services.models import UtcDateTime
from opsmesh_api.workflows.models import _json_document


class EvaluationRun(Base):
    """One suite report for a tenant. Later runs do not replace earlier rows."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_evaluation_runs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    suite_version: Mapped[str] = mapped_column(String(32))
    suite: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    passed: Mapped[int]
    failed: Mapped[int]
    report: Mapped[dict[str, Any]] = mapped_column(_json_document())
