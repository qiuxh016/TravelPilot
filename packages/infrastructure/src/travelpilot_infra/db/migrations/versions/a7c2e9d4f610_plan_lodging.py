"""Persist selected lodging with each immutable itinerary version."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c2e9d4f610"
down_revision = "f5c1a8d73e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trip_plans",
        sa.Column(
            "lodging", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("trip_plans", "lodging")
