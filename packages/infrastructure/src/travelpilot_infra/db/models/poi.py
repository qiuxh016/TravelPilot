from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelpilot_infra.db.base import Base


class PoiModel(Base):
    __tablename__ = "pois"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="CUSTOM",
    )

    address: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )

    latitude: Mapped[Decimal] = mapped_column(
        Numeric(10, 7),
        nullable=False,
    )

    longitude: Mapped[Decimal] = mapped_column(
        Numeric(10, 7),
        nullable=False,
    )

    suggested_duration_min: Mapped[int] = mapped_column(
        nullable=False,
        default=60,
    )

    opening_hours: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    provider_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
