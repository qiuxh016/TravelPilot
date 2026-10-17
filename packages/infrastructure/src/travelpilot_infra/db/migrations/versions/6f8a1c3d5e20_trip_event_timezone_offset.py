"""Preserve the wall-clock offset for itinerary events."""

import sqlalchemy as sa
from alembic import op

revision = "6f8a1c3d5e20"
down_revision = "5e7d9a2c1b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trip_events",
        sa.Column(
            "effective_timezone_offset_minutes",
            sa.SmallInteger(),
            nullable=False,
            server_default="480",
        ),
    )


def downgrade() -> None:
    op.drop_column("trip_events", "effective_timezone_offset_minutes")
