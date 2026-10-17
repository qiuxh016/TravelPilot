"""Add user-owned, explicitly confirmed preference memories."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f5c1a8d73e20"
down_revision = "e48c1a6d2f90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_settings",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "user_preference_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="GENERAL"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("constraint_type", sa.String(16), nullable=False, server_default="PREFERENCE"),
        sa.Column("source", sa.String(32), nullable=False, server_default="USER_EXPLICIT"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("constraint_type IN ('PREFERENCE', 'HARD_LIMIT')", name="ck_user_preference_memories_constraint_type"),
    )
    op.create_index("ix_user_preference_memories_owner_id", "user_preference_memories", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_user_preference_memories_owner_id", table_name="user_preference_memories")
    op.drop_table("user_preference_memories")
    op.drop_table("user_memory_settings")
