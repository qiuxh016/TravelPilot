from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TripDiscussionCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Message cannot be empty")
        return normalized


class TripDiscussionMessageRead(BaseModel):
    id: UUID
    trip_id: UUID
    author_user_id: UUID | None
    author_display_name: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TripActivityRead(BaseModel):
    id: UUID
    trip_id: UUID
    actor_user_id: UUID | None
    actor_display_name: str
    event_type: str
    summary: str
    payload: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
