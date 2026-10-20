"""Add per-user timezone and idempotent trip lifecycle reminders."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b32e5c71a904"
down_revision = "a6e40c2d91bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"))
    op.add_column("users", sa.Column("trip_reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "trip_reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("snoozed_until", sa.Date()),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "trip_id", "kind", "scheduled_for", name="uq_trip_reminder_window"),
    )
    op.create_index("ix_trip_reminders_user_id", "trip_reminders", ["user_id"])
    op.create_index("ix_trip_reminders_trip_id", "trip_reminders", ["trip_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_reminders_trip_id", table_name="trip_reminders")
    op.drop_index("ix_trip_reminders_user_id", table_name="trip_reminders")
    op.drop_table("trip_reminders")
    op.drop_column("users", "trip_reminders_enabled")
    op.drop_column("users", "timezone")
