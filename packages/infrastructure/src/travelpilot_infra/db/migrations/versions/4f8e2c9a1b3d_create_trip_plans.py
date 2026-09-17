"""create versioned trip plans

Revision ID: 4f8e2c9a1b3d
Revises: 9d3eabb9a46a
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "4f8e2c9a1b3d"
down_revision = "9d3eabb9a46a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pois", sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        "trip_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id", "version", name="uq_trip_plan_version"),
    )
    op.create_index("ix_trip_plans_trip_id", "trip_plans", ["trip_id"])
    op.create_table(
        "trip_plan_nodes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("poi_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["trip_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["poi_id"], ["pois.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trip_plan_nodes_plan_id", "trip_plan_nodes", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_plan_nodes_plan_id", table_name="trip_plan_nodes")
    op.drop_table("trip_plan_nodes")
    op.drop_index("ix_trip_plans_trip_id", table_name="trip_plans")
    op.drop_table("trip_plans")
    op.drop_column("pois", "raw_metadata")
