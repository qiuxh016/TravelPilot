"""Record plan authorship and optimistic base versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f31a9c4d2b70"
down_revision = "d7e2b4c819a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trip_plans", sa.Column("base_version", sa.Integer(), nullable=True))
    op.add_column(
        "trip_plans",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "trip_plans",
        sa.Column("created_by_display_name", sa.String(length=80), nullable=True),
    )
    op.add_column("trip_plans", sa.Column("change_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_trip_plans_created_by_user_id_users",
        "trip_plans",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "plan_changes",
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "plan_changes",
        sa.Column("actor_display_name", sa.String(length=80), nullable=True),
    )
    op.create_foreign_key(
        "fk_plan_changes_actor_user_id_users",
        "plan_changes",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_plan_changes_actor_user_id_users", "plan_changes", type_="foreignkey")
    op.drop_column("plan_changes", "actor_display_name")
    op.drop_column("plan_changes", "actor_user_id")
    op.drop_constraint("fk_trip_plans_created_by_user_id_users", "trip_plans", type_="foreignkey")
    op.drop_column("trip_plans", "change_reason")
    op.drop_column("trip_plans", "created_by_display_name")
    op.drop_column("trip_plans", "created_by_user_id")
    op.drop_column("trip_plans", "base_version")
