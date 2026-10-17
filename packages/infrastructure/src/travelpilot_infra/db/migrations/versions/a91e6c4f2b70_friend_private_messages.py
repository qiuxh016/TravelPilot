"""Add one-to-one friend conversations and private messages."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "a91e6c4f2b70"
down_revision = "8b3c1d5e7f90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "friend_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_low_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_high_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_low_analysis_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_high_analysis_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_friend_conversation_pair"),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_friend_conversation_ordered_pair"),
    )
    op.create_index("ix_friend_conversations_user_low_id", "friend_conversations", ["user_low_id"])
    op.create_index("ix_friend_conversations_user_high_id", "friend_conversations", ["user_high_id"])
    op.create_table(
        "friend_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("friend_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.String(4000), nullable=False, server_default=""),
        sa.Column("media_key", sa.String(500), unique=True),
        sa.Column("thumbnail_key", sa.String(500), unique=True),
        sa.Column("mime_type", sa.String(80)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(body)) > 0 OR media_key IS NOT NULL", name="ck_friend_message_content"),
    )
    op.create_index("ix_friend_messages_conversation_id", "friend_messages", ["conversation_id"])
    op.create_index("ix_friend_messages_sender_user_id", "friend_messages", ["sender_user_id"])
    op.create_index("ix_friend_messages_created_at", "friend_messages", ["created_at"])
    op.create_index("ix_friend_messages_conversation_created_at", "friend_messages", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_friend_messages_conversation_created_at", table_name="friend_messages")
    op.drop_index("ix_friend_messages_created_at", table_name="friend_messages")
    op.drop_index("ix_friend_messages_sender_user_id", table_name="friend_messages")
    op.drop_index("ix_friend_messages_conversation_id", table_name="friend_messages")
    op.drop_table("friend_messages")
    op.drop_index("ix_friend_conversations_user_high_id", table_name="friend_conversations")
    op.drop_index("ix_friend_conversations_user_low_id", table_name="friend_conversations")
    op.drop_table("friend_conversations")
