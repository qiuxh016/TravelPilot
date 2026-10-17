"""Add idempotency keys for resumable planner runs."""

import sqlalchemy as sa
from alembic import op

revision = "3c9f2a7b6d10"
down_revision = "2b8d4f6a1c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.create_unique_constraint("uq_agent_run_trip_idempotency", "agent_runs", ["trip_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_agent_run_trip_idempotency", "agent_runs", type_="unique")
    op.drop_column("agent_runs", "idempotency_key")
