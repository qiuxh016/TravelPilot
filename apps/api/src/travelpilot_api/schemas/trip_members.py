from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
    created_at: datetime


class TripMembershipNotice(BaseModel):
    message: str
