"""Track local skill version and extraction token usage for imported content."""

import sqlalchemy as sa
from alembic import op

revision = "c2e7a5d4b130"
down_revision = "b9d3a4e2f610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_sources", sa.Column("skill_name", sa.String(length=80), nullable=True))
    op.add_column("content_sources", sa.Column("skill_version", sa.String(length=24), nullable=True))
    op.add_column("content_sources", sa.Column("token_usage", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("content_sources", "token_usage")
    op.drop_column("content_sources", "skill_version")
    op.drop_column("content_sources", "skill_name")
