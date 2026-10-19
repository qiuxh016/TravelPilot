from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .plans import PlanRead

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
            condition = self.payload.get("condition")
            if condition is None:
                raise ValueError("payload.condition is required for a weather change")
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
        if self.event_type == "USER_ADD":
            hours = self.payload.get("opening_hours")
            if hours is not None:
                if not isinstance(hours, dict):
                    raise ValueError("payload.opening_hours must be an object")
                try:
                    opens = time.fromisoformat(str(hours.get("open", "")))
                    closes = time.fromisoformat(str(hours.get("close", "")))
                except ValueError as exc:
                    raise ValueError("payload.opening_hours must include valid open and close times") from exc
                if closes <= opens:
                    raise ValueError("payload.opening_hours.close must be after open")
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


class ReplanConfirm(BaseModel):
    based_on_plan_id: UUID
    based_on_plan_version: int = Field(ge=1)


class ReplanPreviewNode(BaseModel):
    poi_id: UUID
    name: str
    day: date
    start_time: time
    end_time: time
    priority: int
    status: str
    note: str | None = None


class ReplanPreviewRead(BaseModel):
    run_id: UUID
    event_id: UUID
    based_on_plan_id: UUID
    based_on_plan_version: int
    proposed_version: int
    status: str
    proposed_plan_status: str
    warnings: list[str]
    nodes: list[ReplanPreviewNode]
    diff: list["DiffItem"]
    created_at: datetime
    expires_at: datetime


class ReplanConfirmRead(BaseModel):
    run_id: UUID
    plan: PlanRead
    diff: list[DiffItem]
