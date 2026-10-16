"""Add friendship requests and revocable read-only Trip share grants."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7f2a61d9c30"
down_revision = "a34f6d92c108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "friendships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_low_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_high_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("blocked_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_friendship_pair"),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_friendship_ordered_pair"),
        sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'BLOCKED')", name="ck_friendship_status"),
    )
    op.create_index("ix_friendships_user_low_id", "friendships", ["user_low_id"])
    op.create_index("ix_friendships_user_high_id", "friendships", ["user_high_id"])
    op.create_table(
        "trip_share_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grantee_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="TRIP_SUMMARY"),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("trip_id", "grantee_user_id", name="uq_trip_share_grantee"),
        sa.CheckConstraint("scope IN ('TRIP_SUMMARY', 'TRIP_AND_PLAN')", name="ck_trip_share_scope"),
    )
    op.create_index("ix_trip_share_grants_trip_id", "trip_share_grants", ["trip_id"])
    op.create_index("ix_trip_share_grants_owner_user_id", "trip_share_grants", ["owner_user_id"])
    op.create_index("ix_trip_share_grants_grantee_user_id", "trip_share_grants", ["grantee_user_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_share_grants_grantee_user_id", table_name="trip_share_grants")
    op.drop_index("ix_trip_share_grants_owner_user_id", table_name="trip_share_grants")
    op.drop_index("ix_trip_share_grants_trip_id", table_name="trip_share_grants")
    op.drop_table("trip_share_grants")
    op.drop_index("ix_friendships_user_high_id", table_name="friendships")
    op.drop_index("ix_friendships_user_low_id", table_name="friendships")
    op.drop_table("friendships")
