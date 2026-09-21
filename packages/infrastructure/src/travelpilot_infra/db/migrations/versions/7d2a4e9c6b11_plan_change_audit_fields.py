"""persist stable keys, reason codes and change cost

Revision ID: 7d2a4e9c6b11
Revises: 6c1d0e8b4a22
"""

import sqlalchemy as sa
from alembic import op

revision = "7d2a4e9c6b11"
down_revision = "6c1d0e8b4a22"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plan_changes", sa.Column("stable_key", sa.String(100), nullable=True))
    op.execute("""
        UPDATE plan_changes
        SET stable_key = COALESCE(after_data->>'stable_key', before_data->>'stable_key',
                                  node_id::text, id::text)
    """)
    op.alter_column("plan_changes", "stable_key", nullable=False)
    op.add_column("plan_changes", sa.Column("reason_code", sa.String(50),
                                             nullable=False, server_default="LEGACY"))
    op.add_column("plan_changes", sa.Column("change_cost", sa.Integer(),
                                             nullable=False, server_default="0"))


def downgrade():
    op.drop_column("plan_changes", "change_cost")
    op.drop_column("plan_changes", "reason_code")
    op.drop_column("plan_changes", "stable_key")
