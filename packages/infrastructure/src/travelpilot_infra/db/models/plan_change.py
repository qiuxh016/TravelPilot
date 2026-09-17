from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from travelpilot_infra.db.base import Base


class PlanChangeModel(Base):
    __tablename__ = "plan_changes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_plan_nodes.id", ondelete="SET NULL"), nullable=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    before_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
