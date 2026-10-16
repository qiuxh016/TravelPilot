"""Store advisor planning context and draft revision state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e48c1a6d2f90"
down_revision = "d31b8f42a610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_sessions",
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("advisor_sessions", "context")
