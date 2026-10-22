"""Add unique usernames for account login."""
import re

import sqlalchemy as sa
from alembic import op

revision = "8b3c1d5e7f90"
down_revision = "7a2f4c9d1e60"
branch_labels = None
depends_on = None


def _username_base(email: str) -> str:
    local_part = email.partition("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]+", "_", local_part).strip("_")
    if len(base) < 3 or not base[0].isalnum():
        return "traveler"
    return base[:24]


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=24), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email FROM users ORDER BY created_at, id")).all()
    used: set[str] = set()
    for user_id, email in rows:
        base = _username_base(email)
        username = base
        suffix = 2
        while username in used:
            ending = f"_{suffix}"
            username = f"{base[:24 - len(ending)]}{ending}"
            suffix += 1
        used.add(username)
        bind.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :user_id"),
            {"username": username, "user_id": user_id},
        )
    op.alter_column("users", "username", nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
