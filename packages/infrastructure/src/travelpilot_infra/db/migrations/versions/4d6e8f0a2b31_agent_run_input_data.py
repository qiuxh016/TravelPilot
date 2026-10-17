"""Persist planner input required for idempotent recovery validation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4d6e8f0a2b31"
down_revision = "3c9f2a7b6d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("input_data", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "input_data")
