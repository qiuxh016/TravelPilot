"""Add editable user profile fields and private avatar references."""
import sqlalchemy as sa
from alembic import op

revision = "7a2f4c9d1e60"
down_revision = "6f8a1c3d5e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=48), nullable=False, server_default=""))
    op.add_column("users", sa.Column("home_city", sa.String(length=120), nullable=False, server_default=""))
    op.add_column("users", sa.Column("bio", sa.Text(), nullable=False, server_default=""))
    op.add_column("users", sa.Column("avatar_key", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("avatar_thumbnail_key", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("avatar_mime_type", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_mime_type")
    op.drop_column("users", "avatar_thumbnail_key")
    op.drop_column("users", "avatar_key")
    op.drop_column("users", "bio")
    op.drop_column("users", "home_city")
    op.drop_column("users", "display_name")
