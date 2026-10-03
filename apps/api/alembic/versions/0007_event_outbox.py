"""Outbox and consumer projections for domain events.

Revision ID: 0007_event_outbox
Revises: 0006_approval_audit
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_event_outbox"
down_revision: str | None = "0006_approval_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "'WorkflowStarted', 'TaskDelegated', 'TaskCompleted', 'TaskFailed', "
    "'ProposalCreated', 'ApprovalRequested', 'ApprovalGranted', 'ApprovalRejected', "
    "'DraftPOCreated', 'WorkflowCompleted', 'WorkflowFailed'"
)


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(f"event_type IN ({_EVENT_TYPES})", name="ck_event_outbox_type"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_event_outbox_attempts"),
        sa.CheckConstraint("sequence >= 1", name="ck_event_outbox_sequence"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_event_outbox_unpublished", "event_outbox", ["published_at", "sequence"])
    op.create_index("ix_event_outbox_workflow", "event_outbox", ["tenant_id", "workflow_id"])
    op.create_table(
        "event_consumer_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_event_consumer_receipt"),
    )
    op.create_index("ix_event_consumer_receipts_event", "event_consumer_receipts", ["event_id"])
    op.create_table(
        "audit_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"event_type IN ({_EVENT_TYPES})", name="ck_audit_projections_type"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
        sa.UniqueConstraint("event_id", name="uq_audit_projections_event"),
    )
    op.create_index("ix_audit_projections_tenant", "audit_projections", ["tenant_id", "position"])
    op.create_table(
        "notification_inbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
        sa.UniqueConstraint("event_id", name="uq_notification_inbox_event"),
    )
    op.create_index(
        "ix_notification_inbox_tenant",
        "notification_inbox",
        ["tenant_id", "recorded_at"],
    )
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
        sa.UniqueConstraint("event_id", name="uq_analytics_events_event"),
    )
    op.create_index("ix_analytics_events_tenant", "analytics_events", ["tenant_id", "event_type"])
    op.create_table(
        "evaluation_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
        sa.UniqueConstraint("event_id", name="uq_evaluation_samples_event"),
        sa.UniqueConstraint("tenant_id", "workflow_id", name="uq_evaluation_samples_workflow"),
    )
    op.create_index("ix_evaluation_samples_tenant", "evaluation_samples", ["tenant_id"])
    op.create_table(
        "proposal_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("purchase_order_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_runs.id"]),
        sa.UniqueConstraint("event_id", name="uq_proposal_effects_event"),
    )
    op.create_index("ix_proposal_effects_tenant", "proposal_effects", ["tenant_id", "workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_proposal_effects_tenant", table_name="proposal_effects")
    op.drop_table("proposal_effects")
    op.drop_index("ix_evaluation_samples_tenant", table_name="evaluation_samples")
    op.drop_table("evaluation_samples")
    op.drop_index("ix_analytics_events_tenant", table_name="analytics_events")
    op.drop_table("analytics_events")
    op.drop_index("ix_notification_inbox_tenant", table_name="notification_inbox")
    op.drop_table("notification_inbox")
    op.drop_index("ix_audit_projections_tenant", table_name="audit_projections")
    op.drop_table("audit_projections")
    op.drop_index("ix_event_consumer_receipts_event", table_name="event_consumer_receipts")
    op.drop_table("event_consumer_receipts")
    op.drop_index("ix_event_outbox_workflow", table_name="event_outbox")
    op.drop_index("ix_event_outbox_unpublished", table_name="event_outbox")
    op.drop_table("event_outbox")
