"""Add opt-in trip membership and collaboration roles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d7e2b4c819a1"
down_revision = "e8a1f3c6d902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "trip_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "invited_by_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="VIEWER"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("trip_id", "user_id", name="uq_trip_member_user"),
        sa.CheckConstraint("role IN ('EDITOR', 'VIEWER')", name="ck_trip_member_role"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'DECLINED', 'REVOKED')",
            name="ck_trip_member_status",
        ),
    )
    op.create_index("ix_trip_members_trip_id", "trip_members", ["trip_id"])
    op.create_index("ix_trip_members_user_status", "trip_members", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_trip_members_user_status", table_name="trip_members")
    op.drop_index("ix_trip_members_trip_id", table_name="trip_members")
    op.drop_table("trip_members")
