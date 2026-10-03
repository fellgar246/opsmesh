"""Store the supervisor plan on the workflow run.

Revision ID: 0003_supervisor_plan
Revises: 0002_workflow
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_supervisor_plan"
down_revision: str | None = "0002_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("plan", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "plan")
