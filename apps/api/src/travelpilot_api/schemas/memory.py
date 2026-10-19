from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=5)


class MemoryRead(BaseModel):
    id: UUID
    trip_id: UUID
    feedback_id: UUID | None
    memory_type: str
    text: str
    importance: float
    confidence: float
    confirmed: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class FeedbackRead(BaseModel):
    id: UUID
    trip_id: UUID
    text: str
    rating: int | None
    created_at: datetime
    candidate_memory: MemoryRead | None


class MemoryUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=2, max_length=2000)
    confirmed: bool | None = None


class UserPreferenceMemoryCreate(BaseModel):
    category: str = Field(default="GENERAL", min_length=2, max_length=40)
    text: str = Field(min_length=2, max_length=1000)
    constraint_type: str = Field(default="PREFERENCE", pattern="^(PREFERENCE|HARD_LIMIT)$")
    expires_at: datetime | None = None


class UserPreferenceMemorySuggestionConfirm(BaseModel):
    category: str = Field(pattern="^(PACE|INTEREST|FOOD|LODGING|TRANSPORT|GENERAL)$")
    text: str = Field(min_length=2, max_length=240)
    constraint_type: str = Field(pattern="^(PREFERENCE|HARD_LIMIT)$")


class UserPreferenceMemoryUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=2, max_length=40)
    text: str | None = Field(default=None, min_length=2, max_length=1000)
    constraint_type: str | None = Field(default=None, pattern="^(PREFERENCE|HARD_LIMIT)$")
    expires_at: datetime | None = None


class UserPreferenceMemoryRead(BaseModel):
    id: UUID
    category: str
    text: str
    constraint_type: str
    source: str
    confidence: float
    confirmed: bool
    expires_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class UserMemorySettingsRead(BaseModel):
    enabled: bool


class UserMemorySettingsUpdate(BaseModel):
    enabled: bool
