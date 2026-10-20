"""Persist structured facts extracted from travel guides."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c6d8e0a2b491"
down_revision = "a91e6c4f2b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_sources",
        sa.Column(
            "guide_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("content_sources", "guide_facts")
