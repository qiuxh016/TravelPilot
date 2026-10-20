from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TripCreate(BaseModel):
    destination: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date
    budget: Decimal | None = Field(default=None, ge=0)
    pace_level: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def validate_dates(self) -> "TripCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class TripUpdate(BaseModel):
    destination: str | None = Field(default=None, min_length=1, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0)
    pace_level: int | None = Field(default=None, ge=1, le=5)


class TripRead(TripCreate):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    has_plan: bool = False
    access_role: Literal["OWNER", "EDITOR", "VIEWER"] = "OWNER"
    lifecycle_status: Literal["UPCOMING", "ONGOING", "COMPLETED"] = "UPCOMING"

    model_config = {
        "from_attributes": True,
    }


class TripReminderAction(BaseModel):
    action: Literal["DONE", "LATER", "DISMISS"]


class TripReminderRead(BaseModel):
    id: UUID
    trip_id: UUID
    kind: Literal["PLAN_7D", "PLAN_2D", "AFTER_TRIP"]
    destination: str
    start_date: date
    end_date: date
    scheduled_for: date
    status: Literal["PENDING", "SNOOZED", "DONE", "DISMISSED"]
