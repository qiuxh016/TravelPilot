"""Persist owner-scoped advisor sessions, messages, and unconfirmed drafts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d31b8f42a610"
down_revision = "c8e0d532a714"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="SET NULL")),
        sa.Column("confirmed_trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="SET NULL")),
        sa.Column("confirmed_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trip_plans.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("destination", sa.String(120), nullable=False, server_default=""),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("budget", sa.Numeric(12, 2)),
        sa.Column("pace_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("preference_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidates", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("draft", postgresql.JSONB()),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mode IN ('discover', 'destination', 'existing')", name="ck_advisor_session_mode"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CONFIRMED')", name="ck_advisor_session_status"),
        sa.CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_advisor_session_dates"),
    )
    op.create_index("ix_advisor_sessions_owner_id", "advisor_sessions", ["owner_id"])
    op.create_index("ix_advisor_sessions_trip_id", "advisor_sessions", ["trip_id"])

    op.create_table(
        "advisor_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("advisor_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_advisor_message_role"),
    )
    op.create_index("ix_advisor_messages_session_id", "advisor_messages", ["session_id"])
    op.create_index("ix_advisor_messages_owner_id", "advisor_messages", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_advisor_messages_owner_id", table_name="advisor_messages")
    op.drop_index("ix_advisor_messages_session_id", table_name="advisor_messages")
    op.drop_table("advisor_messages")
    op.drop_index("ix_advisor_sessions_trip_id", table_name="advisor_sessions")
    op.drop_index("ix_advisor_sessions_owner_id", table_name="advisor_sessions")
    op.drop_table("advisor_sessions")
