from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FriendRequestCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class FriendRequestNotice(BaseModel):
    message: str


class FriendshipRead(BaseModel):
    id: UUID
    friend_user_id: UUID
    email: str
    status: str
    direction: str
    created_at: datetime


class TripShareCreate(BaseModel):
    friend_user_id: UUID
    scope: str = "TRIP_SUMMARY"


class TripShareRead(BaseModel):
    id: UUID
    trip_id: UUID
    grantee_user_id: UUID
    email: str
    scope: str
    created_at: datetime


class SharedTripRead(BaseModel):
    id: UUID
    destination: str
    start_date: date
    end_date: date
    scope: str
    owner_email: str


class SharedTripPlanNodeRead(BaseModel):
    name: str
    day: date
    start_time: str
    end_time: str
    status: str


class SharedTripDetailRead(SharedTripRead):
    plan_version: int | None = None
    plan_nodes: list[SharedTripPlanNodeRead] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
