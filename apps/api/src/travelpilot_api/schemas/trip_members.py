from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TripMemberRole = Literal["EDITOR", "VIEWER"]
TripMemberStatus = Literal["PENDING", "ACCEPTED", "DECLINED", "REVOKED"]


class TripMemberInvite(BaseModel):
    user_id: UUID
    role: TripMemberRole = "VIEWER"


class TripMemberRoleUpdate(BaseModel):
    role: TripMemberRole


class TripMemberRead(BaseModel):
    id: UUID
    trip_id: UUID
    user_id: UUID
    username: str
    display_name: str
    has_avatar: bool
    role: TripMemberRole
    status: TripMemberStatus
    invited_by_id: UUID | None
    accepted_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TripInvitationRead(BaseModel):
    id: UUID
    inviter_display_name: str
    role: TripMemberRole
    created_at: datetime


class TripMembershipNotice(BaseModel):
    message: str


class CollaborativeTripRead(BaseModel):
    id: UUID
    destination: str
    start_date: date
    end_date: date
    owner_display_name: str
    role: Literal["EDITOR", "VIEWER"]


class CollaborativePlanNodeRead(BaseModel):
    id: UUID
    poi_id: UUID
    name: str
    day: date
    start_time: str
    end_time: str
    priority: int
    status: str
    note: str | None = None


class CollaborativeTripDetailRead(CollaborativeTripRead):
    plan_id: UUID | None = None
    plan_version: int | None = None
    plan_nodes: list[CollaborativePlanNodeRead] = Field(default_factory=list)
