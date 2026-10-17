"""Add privacy-safe advisor run and user action events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4a9c6e17b30"
down_revision = "c2e7a5d4b130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("advisor_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(50)),
        sa.Column("token_usage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_advisor_run_events_owner_id", "advisor_run_events", ["owner_id"])
    op.create_index("ix_advisor_run_events_session_id", "advisor_run_events", ["session_id"])
    op.create_index("ix_advisor_run_events_created_at", "advisor_run_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_advisor_run_events_created_at", table_name="advisor_run_events")
    op.drop_index("ix_advisor_run_events_session_id", table_name="advisor_run_events")
    op.drop_index("ix_advisor_run_events_owner_id", table_name="advisor_run_events")
    op.drop_table("advisor_run_events")
