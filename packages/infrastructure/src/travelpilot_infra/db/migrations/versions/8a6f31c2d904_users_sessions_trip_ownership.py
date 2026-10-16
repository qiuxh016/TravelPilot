"""Add accounts, revocable sessions, and Trip ownership.

Revision ID: 8a6f31c2d904
Revises: 7d2a4e9c6b11
"""
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8a6f31c2d904"
down_revision = "7d2a4e9c6b11"
branch_labels = None
depends_on = None

LEGACY_OWNER_ID = uuid4()


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # Preserve pre-auth trips under a disabled account; never expose them to the first signup.
    op.execute(
        sa.text("""INSERT INTO users (id, email, password_hash, is_active)
                    VALUES (:id, 'legacy-owner@travelpilot.invalid', '!disabled-migrated-account!', false)""")
        .bindparams(id=LEGACY_OWNER_ID)
    )
    op.add_column("trips", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(sa.text("UPDATE trips SET owner_id = :owner_id").bindparams(owner_id=LEGACY_OWNER_ID))
    op.alter_column("trips", "owner_id", nullable=False)
    op.create_foreign_key("fk_trips_owner_id_users", "trips", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_trips_owner_id", "trips", ["owner_id"])


def downgrade():
    op.drop_index("ix_trips_owner_id", table_name="trips")
    op.drop_constraint("fk_trips_owner_id_users", "trips", type_="foreignkey")
    op.drop_column("trips", "owner_id")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
