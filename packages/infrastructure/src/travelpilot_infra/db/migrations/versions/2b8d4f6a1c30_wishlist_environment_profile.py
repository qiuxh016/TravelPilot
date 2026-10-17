"""Keep user-curated environmental attributes scoped to one trip wishlist."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2b8d4f6a1c30"
down_revision = "1a7c3e9d5b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wishlist_items",
        sa.Column("environment_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wishlist_items", "environment_profile")
