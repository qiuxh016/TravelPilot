"""Add sourced environmental attributes for weather-aware replanning."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "1a7c3e9d5b20"
down_revision = "d4a9c6e17b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pois",
        sa.Column("environment_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pois", "environment_profile")
