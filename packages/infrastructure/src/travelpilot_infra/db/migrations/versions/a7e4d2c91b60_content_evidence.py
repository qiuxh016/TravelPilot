"""Preserve source evidence for imported travel candidates."""

import sqlalchemy as sa
from alembic import op

revision = "a7e4d2c91b60"
down_revision = "f5c1a8d73e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extracted_entities", sa.Column("evidence", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_entities", "evidence")
