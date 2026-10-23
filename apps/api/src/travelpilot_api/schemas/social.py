from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FriendRequestCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class FriendRequestNotice(BaseModel):
    message: str


class FriendSearchResult(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    email_hint: str
    has_avatar: bool
    is_self: bool = False


class FriendConversationRead(BaseModel):
    id: UUID
    friend_user_id: UUID
    username: str
    display_name: str
    self_analysis_consent: bool = False
    friend_analysis_consent: bool = False


class FriendAnalysisConsentUpdate(BaseModel):
    enabled: bool


class FriendAnalysisRead(BaseModel):
    summary: str
    observed_tone: str
    travel_preferences: list[str] = Field(default_factory=list)
    suggested_reply: str
    evidence: list[str] = Field(default_factory=list)
    includes_images: bool


class FriendMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class FriendMessageRead(BaseModel):
    id: UUID
    sender_user_id: UUID
    body: str
    has_media: bool
    mime_type: str | None = None
    created_at: datetime


class FriendshipRead(BaseModel):
    id: UUID
    friend_user_id: UUID
    email: str
    display_name: str = ""
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
    lodging: list[dict] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
