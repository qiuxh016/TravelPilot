from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

EVENT_TYPES = {"WEATHER_CHANGE", "USER_FATIGUE", "POI_CANCELLED", "TIME_DELAY", "USER_ADD", "RESERVATION_CHANGE"}
WEATHER_CONDITIONS = {"RAIN", "WIND", "HEAT", "COLD", "SNOW", "STORM"}


class EventCreate(BaseModel):
    event_type: str
    effective_at: datetime
    payload: dict = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def valid_type(cls, value):
        if value not in EVENT_TYPES:
            raise ValueError("Unknown event type")
        return value

    @field_validator("effective_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("effective_at requires a timezone")
        return value


    @model_validator(mode="after")
    def valid_payload(self):
        if self.event_type == "WEATHER_CHANGE":
            condition = self.payload.get("condition", "RAIN")
            if condition not in WEATHER_CONDITIONS:
                raise ValueError("payload.condition must be a supported weather condition")
        if self.event_type in {"POI_CANCELLED", "USER_ADD", "RESERVATION_CHANGE"}:
            try:
                UUID(str(self.payload.get("poi_id", "")))
            except ValueError as exc:
                raise ValueError("payload.poi_id must be a UUID") from exc
        if self.event_type == "TIME_DELAY":
            value = self.payload.get("delay_min")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 180:
                raise ValueError("payload.delay_min must be 1..180")
        return self


class EventRead(EventCreate):
    id: UUID
    trip_id: UUID
    created_at: datetime
    effective_timezone_offset_minutes: int = 480
    model_config = {"from_attributes": True}


class ReplanRequest(BaseModel):
    event_id: UUID


class DiffItem(BaseModel):
    stable_key: str
    operation: str
    reason_code: str
    before: dict | None
    after: dict | None
    change_cost: int
