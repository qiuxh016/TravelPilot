from datetime import date, datetime, time, timezone
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Time, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelpilot_infra.db.base import Base


class TripPlanModel(Base):
    __tablename__ = "trip_plans"
    __table_args__ = (UniqueConstraint("trip_id", "version", name="uq_trip_plan_version"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    trip_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class TripPlanNodeModel(Base):
    __tablename__ = "trip_plan_nodes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    poi_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("pois.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNED")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
