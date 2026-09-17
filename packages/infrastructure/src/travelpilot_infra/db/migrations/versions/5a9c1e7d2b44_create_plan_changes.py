"""create plan changes

Revision ID: 5a9c1e7d2b44
Revises: 4f8e2c9a1b3d
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "5a9c1e7d2b44"
down_revision = "4f8e2c9a1b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_changes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.UUID(), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("before_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["trip_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["trip_plan_nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_changes_plan_id", "plan_changes", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_changes_plan_id", table_name="plan_changes")
    op.drop_table("plan_changes")
