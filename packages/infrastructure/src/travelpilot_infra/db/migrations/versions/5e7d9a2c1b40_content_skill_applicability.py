"""Persist the redacted Skill applicability decision for imported content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5e7d9a2c1b40"
down_revision = "4d6e8f0a2b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_sources",
        sa.Column("skill_applicability", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_sources", "skill_applicability")
