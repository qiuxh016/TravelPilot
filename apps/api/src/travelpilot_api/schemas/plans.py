from datetime import date, time, datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
    nodes: list[PlanNodeRead]


class PlanNodeUpdate(BaseModel):
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
    before_data: dict | None
    after_data: dict | None
    reason: str | None
    created_at: datetime
