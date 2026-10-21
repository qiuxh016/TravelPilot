from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AdvisorRuntimeStatus(BaseModel):
    llm_status: Literal["CONFIGURED", "NOT_CONFIGURED"]
    model_name: str | None = None
    output_token_limit: int
    session_output_token_limit: int
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
    selected_candidate_ids: list[UUID] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_selection(self):
        if self.selected_candidate_ids is not None and len(set(self.selected_candidate_ids)) != len(self.selected_candidate_ids):
            raise ValueError("selected_candidate_ids must be unique")
        return self


class AdvisorMessageFeedbackCreate(BaseModel):
    rating: Literal["HELPFUL", "NOT_HELPFUL"]
    reason: Literal["MISSED_REQUEST", "INACCURATE", "NOT_ACTIONABLE", "TOO_VERBOSE", "OTHER"] | None = None

    @model_validator(mode="after")
    def validate_reason(self):
        if self.rating == "HELPFUL" and self.reason is not None:
            raise ValueError("helpful feedback cannot include a negative reason")
        return self


class AdvisorMessageFeedbackRead(BaseModel):
    message_id: UUID
    rating: Literal["HELPFUL", "NOT_HELPFUL"]
    reason: Literal["MISSED_REQUEST", "INACCURATE", "NOT_ACTIONABLE", "TOO_VERBOSE", "OTHER"] | None
    submitted_at: datetime


class AdvisorDestinationDiscoveryRequest(BaseModel):
    city: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def normalize_city(self):
        if self.city is not None:
            self.city = self.city.strip()
            if not self.city:
                raise ValueError("city must not be blank")
        return self


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


class AdvisorCandidateScheduleState(BaseModel):
    candidate_id: UUID
    day: date | None = None
    start_time: time | None = None

    @model_validator(mode="after")
    def validate_schedule_state(self):
        if self.day is None and self.start_time is None:
            raise ValueError("a schedule state must include a day or start_time")
        return self


class AdvisorSelectionUpdate(BaseModel):
    selected_candidate_ids: list[UUID] = Field(max_length=100)
    schedules: list[AdvisorCandidateScheduleState] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_selection_state(self):
        selected_ids = [item for item in self.selected_candidate_ids]
        schedule_ids = [item.candidate_id for item in self.schedules]
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("selected_candidate_ids must be unique")
        if len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("schedule candidate_ids must be unique")
        if not set(schedule_ids).issubset(set(selected_ids)):
            raise ValueError("schedules must belong to selected candidates")
        return self


class AdvisorDraftCreate(BaseModel):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=30)
    schedules: list[AdvisorCandidateSchedule] = Field(default_factory=list, max_length=30)
    allow_profile_memory_conflicts: bool = False


class AdvisorNegotiationRequest(BaseModel):
    draft_version: int = Field(ge=1)


class AdvisorNegotiationChange(BaseModel):
    candidate_id: UUID
    operation: Literal["MOVE", "REMOVE"]
    day: date | None = None
    start_time: time | None = None


class AdvisorNegotiationAlternativeRead(BaseModel):
    id: str
    title: str
    summary: str
    change_cost: int
    changes: list[AdvisorNegotiationChange]


class AdvisorNegotiationRead(BaseModel):
    draft_version: int
    status: Literal["NO_CONFLICT", "OPTIONS", "NO_FEASIBLE_OPTION"]
    conflicts: list[str] = Field(default_factory=list)
    alternatives: list[AdvisorNegotiationAlternativeRead] = Field(default_factory=list)


class AdvisorChecklistDecision(BaseModel):
    item_key: str = Field(min_length=3, max_length=220, pattern=r"^[A-Z_]+:\d{4}-\d{2}-\d{2}(?::[A-Z]+(?::[^\r\n]{0,80})?)?$" )
    decision: Literal["LATER", "NOT_NEEDED", "RESET"]
    draft_version: int = Field(ge=1)


class AdvisorConfirm(BaseModel):
    draft_version: int = Field(ge=1)


class AdvisorMessageRead(BaseModel):
    id: UUID
    role: str
    content: str
    payload: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdvisorProgressEventRead(BaseModel):
    id: UUID
    phase: str
    operation: str
    status: str
    metadata: dict
    created_at: datetime


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
