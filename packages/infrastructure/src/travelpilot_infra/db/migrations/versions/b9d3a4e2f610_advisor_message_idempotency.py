"""Add per-session idempotency keys for advisor messages."""

import sqlalchemy as sa
from alembic import op

revision = "b9d3a4e2f610"
down_revision = "a7e4d2c91b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("advisor_messages", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.create_unique_constraint("uq_advisor_message_idempotency", "advisor_messages", ["session_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_advisor_message_idempotency", "advisor_messages", type_="unique")
    op.drop_column("advisor_messages", "idempotency_key")
