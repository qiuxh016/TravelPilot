"""Add private trip discussion and activity history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a6e40c2d91bf"
down_revision = "f31a9c4d2b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip_discussion_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_display_name", sa.String(length=80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trip_discussion_messages_trip_created", "trip_discussion_messages", ["trip_id", "created_at"])
    op.create_table(
        "trip_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_display_name", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trip_activities_trip_created", "trip_activities", ["trip_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_trip_activities_trip_created", table_name="trip_activities")
    op.drop_table("trip_activities")
    op.drop_index("ix_trip_discussion_messages_trip_created", table_name="trip_discussion_messages")
    op.drop_table("trip_discussion_messages")
