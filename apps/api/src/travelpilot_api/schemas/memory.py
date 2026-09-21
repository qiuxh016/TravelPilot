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
