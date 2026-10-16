"""Add private by default travel moments, media, comments, reactions, and memory suggestions."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8e0d532a714"
down_revision = "b7f2a61d9c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "travel_moments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="SET NULL")),
        sa.Column("day", sa.Date()),
        sa.Column("place_name", sa.String(160)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False, server_default="PRIVATE"),
        sa.Column("memory_extraction_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("audience IN ('PRIVATE', 'FRIENDS')", name="ck_travel_moment_audience"),
    )
    op.create_index("ix_travel_moments_owner_user_id", "travel_moments", ["owner_user_id"])
    op.create_index("ix_travel_moments_trip_id", "travel_moments", ["trip_id"])
    op.create_index("ix_travel_moments_created_at", "travel_moments", ["created_at"])
    op.create_table(
        "moment_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("moment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("travel_moments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False, unique=True),
        sa.Column("thumbnail_key", sa.String(500), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("exif_stripped", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("size_bytes > 0", name="ck_moment_media_positive_size"),
    )
    op.create_index("ix_moment_media_moment_id", "moment_media", ["moment_id"])
    op.create_index("ix_moment_media_owner_user_id", "moment_media", ["owner_user_id"])
    op.create_table(
        "moment_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("moment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("travel_moments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_moment_comments_moment_id", "moment_comments", ["moment_id"])
    op.create_index("ix_moment_comments_author_user_id", "moment_comments", ["author_user_id"])
    op.create_table(
        "moment_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("moment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("travel_moments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="LIKE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("moment_id", "user_id", name="uq_moment_reaction_user"),
    )
    op.create_index("ix_moment_reactions_moment_id", "moment_reactions", ["moment_id"])
    op.create_index("ix_moment_reactions_user_id", "moment_reactions", ["user_id"])
    op.create_table(
        "memory_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_moment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("travel_moments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate", sa.String(2000), nullable=False),
        sa.Column("evidence", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('PENDING', 'CONFIRMED', 'REJECTED')", name="ck_memory_suggestion_status"),
    )
    op.create_index("ix_memory_suggestions_owner_user_id", "memory_suggestions", ["owner_user_id"])
    op.create_index("ix_memory_suggestions_source_moment_id", "memory_suggestions", ["source_moment_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_suggestions_source_moment_id", table_name="memory_suggestions")
    op.drop_index("ix_memory_suggestions_owner_user_id", table_name="memory_suggestions")
    op.drop_table("memory_suggestions")
    op.drop_index("ix_moment_reactions_user_id", table_name="moment_reactions")
    op.drop_index("ix_moment_reactions_moment_id", table_name="moment_reactions")
    op.drop_table("moment_reactions")
    op.drop_index("ix_moment_comments_author_user_id", table_name="moment_comments")
    op.drop_index("ix_moment_comments_moment_id", table_name="moment_comments")
    op.drop_table("moment_comments")
    op.drop_index("ix_moment_media_owner_user_id", table_name="moment_media")
    op.drop_index("ix_moment_media_moment_id", table_name="moment_media")
    op.drop_table("moment_media")
    op.drop_index("ix_travel_moments_created_at", table_name="travel_moments")
    op.drop_index("ix_travel_moments_trip_id", table_name="travel_moments")
    op.drop_index("ix_travel_moments_owner_user_id", table_name="travel_moments")
    op.drop_table("travel_moments")
