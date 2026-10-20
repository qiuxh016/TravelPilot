from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelpilot_infra.db.base import Base


class TripMemberModel(Base):
    """An invited non-owner member of a private or group trip."""

    __tablename__ = "trip_members"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", name="uq_trip_member_user"),
        CheckConstraint("role IN ('EDITOR', 'VIEWER')", name="ck_trip_member_role"),
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'DECLINED', 'REVOKED')",
            name="ck_trip_member_status",
        ),
        Index("ix_trip_members_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    trip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    invited_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="VIEWER", server_default="VIEWER")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), server_default=func.now(),
    )
