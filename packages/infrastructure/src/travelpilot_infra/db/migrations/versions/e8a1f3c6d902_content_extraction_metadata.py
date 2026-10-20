"""Persist guide-import chunk coverage and retry ranges."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8a1f3c6d902"
down_revision = "c6d8e0a2b491"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_sources",
        sa.Column(
            "extraction_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("content_sources", "extraction_metadata")
