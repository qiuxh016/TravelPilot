from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanNodeRead(BaseModel):
    id: UUID
    poi_id: UUID
    name: str
    day: date
    start_time: time
    end_time: time
    priority: int
    status: str
    note: str | None = None


class PlanRead(BaseModel):
    id: UUID
    trip_id: UUID
    version: int
    status: str
    warnings: list[str]
    created_at: datetime
    base_version: int | None = None
    created_by_user_id: UUID | None = None
    created_by_display_name: str | None = None
    change_reason: str | None = None
    nodes: list[PlanNodeRead]


class PlanRunRequest(BaseModel):
    expected_base_version: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=500)


class PlanNodeUpdate(BaseModel):
    expected_base_version: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=500)
    day: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: str | None = Field(default=None, max_length=32)
    note: str | None = None


class PlanChangeRead(BaseModel):
    id: UUID
    plan_id: UUID
    node_id: UUID | None
    change_type: str
    stable_key: str
    reason_code: str
    change_cost: int
    before_data: dict | None
    after_data: dict | None
    reason: str | None
    actor_user_id: UUID | None = None
    actor_display_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
