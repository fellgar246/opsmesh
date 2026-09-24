"""Workflow system of record.

Revision ID: 0002_workflow
Revises: 0001_enterprise
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_workflow"
down_revision: str | None = "0001_enterprise"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input", _JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_summary", _JSON, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_APPROVAL', 'COMPLETED', 'FAILED', "
            "'CANCELLED')",
            name="ck_workflow_runs_status",
        ),
    )
    op.create_index("ix_workflow_runs_tenant_id", "workflow_runs", ["tenant_id", "id"])
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_type", sa.String(length=64), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("depends_on", _JSON, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["parent_task_id"], ["agent_tasks.id"]),
        sa.CheckConstraint(
            "status IN ('PENDING', 'READY', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', "
            "'SKIPPED')",
            name="ck_agent_tasks_status",
        ),
    )
    op.create_index("ix_agent_tasks_run", "agent_tasks", ["tenant_id", "workflow_run_id"])
    op.create_table(
        "agent_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("facts", _JSON, nullable=False),
        sa.Column("evidence", _JSON, nullable=False),
        sa.Column("warnings", _JSON, nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_agent_results_confidence",
        ),
    )
    op.create_index("ix_agent_results_task", "agent_results", ["tenant_id", "task_id"])
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("source_record_id", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_evidence_run", "evidence", ["tenant_id", "workflow_run_id"])
    op.create_table(
        "operational_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_type", sa.String(length=64), nullable=False),
        sa.Column("arguments", _JSON, nullable=False),
        sa.Column("estimated_impact", _JSON, nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'EXPIRED', "
            "'SUPERSEDED')",
            name="ck_operational_proposals_status",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_operational_proposals_risk",
        ),
    )
    op.create_index(
        "ix_operational_proposals_run",
        "operational_proposals",
        ["tenant_id", "workflow_run_id"],
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("required_role", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["operational_proposals.id"]),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_approvals_status",
        ),
        sa.CheckConstraint(
            "required_role IN ('viewer', 'operations_analyst', 'procurement_manager', "
            "'finance_approver', 'admin')",
            name="ck_approvals_role",
        ),
    )
    op.create_index("ix_approvals_proposal", "approvals", ["tenant_id", "proposal_id"])
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_reference", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name="ck_tool_executions_status",
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_tool_executions_latency"),
        sa.CheckConstraint("retry_count >= 0", name="ck_tool_executions_retries"),
    )
    op.create_index("ix_tool_executions_task", "tool_executions", ["tenant_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_executions_task", table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index("ix_approvals_proposal", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_operational_proposals_run", table_name="operational_proposals")
    op.drop_table("operational_proposals")
    op.drop_index("ix_evidence_run", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_agent_results_task", table_name="agent_results")
    op.drop_table("agent_results")
    op.drop_index("ix_agent_tasks_run", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_index("ix_workflow_runs_tenant_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
