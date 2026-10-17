from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AdvisorRuntimeStatus(BaseModel):
    llm_status: Literal["CONFIGURED", "NOT_CONFIGURED"]
    model_name: str | None = None
    output_token_limit: int
    upstream_status: Literal["CHECKED_PER_REQUEST"] = "CHECKED_PER_REQUEST"


class AdvisorSessionCreate(BaseModel):
    mode: Literal["discover", "destination", "existing"] = "destination"
    trip_id: UUID | None = None
    destination: str = Field(default="", max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0)
    pace_level: int = Field(default=3, ge=1, le=5)
    preference_text: str = Field(default="", max_length=4000)
    origin: str = Field(default="", max_length=120)
    companions: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=10)
    hard_constraints: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_context(self):
        if self.mode == "existing" and self.trip_id is None:
            raise ValueError("trip_id is required for an existing trip")
        if self.mode == "destination" and not self.destination.strip():
            raise ValueError("destination is required")
        if self.mode == "destination" and (self.start_date is None or self.end_date is None):
            raise ValueError("start_date and end_date are required")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be provided together")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class AdvisorMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AdvisorSessionContextUpdate(BaseModel):
    destination: str = Field(default="", max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0)
    pace_level: int = Field(default=3, ge=1, le=5)
    preference_text: str = Field(default="", max_length=4000)
    origin: str = Field(default="", max_length=120)
    companions: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=10)
    hard_constraints: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_context(self):
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be provided together")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class AdvisorCandidateSchedule(BaseModel):
    candidate_id: UUID
    day: date
    start_time: time


class AdvisorDraftCreate(BaseModel):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=30)
    schedules: list[AdvisorCandidateSchedule] = Field(min_length=1, max_length=30)


class AdvisorConfirm(BaseModel):
    draft_version: int = Field(ge=1)


class AdvisorMessageRead(BaseModel):
    id: UUID
    role: str
    content: str
    payload: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdvisorSessionRead(BaseModel):
    id: UUID
    mode: str
    trip_id: UUID | None
    destination: str
    start_date: date | None
    end_date: date | None
    budget: Decimal | None
    pace_level: int
    preference_text: str
    candidates: list[dict]
    context: dict
    draft: dict | None
    draft_version: int
    draft_stale: bool = False
    status: str
    confirmed_trip_id: UUID | None
    confirmed_plan_id: UUID | None
    created_at: datetime
    updated_at: datetime
    messages: list[AdvisorMessageRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class AdvisorConfirmRead(BaseModel):
    trip_id: UUID
    plan_id: UUID
    plan_version: int
    already_confirmed: bool = False
