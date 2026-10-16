"""Persist bounded login failure counters.

Revision ID: 9c7e2a11f403
Revises: 8a6f31c2d904
"""
import sqlalchemy as sa
from alembic import op

revision = "9c7e2a11f403"
down_revision = "8a6f31c2d904"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_attempts",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("login_attempts")
