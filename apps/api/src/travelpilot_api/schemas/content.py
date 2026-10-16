from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ContentCreate(BaseModel):
    content_type: str = Field(default="TEXT", pattern="^TEXT$")
    text: str = Field(min_length=2, max_length=20000)


class OcrTextRead(BaseModel):
    text: str
    confidence: float | None
    stored: bool = False


class CandidateRead(BaseModel):
    id: UUID
    entity_id: UUID
    provider: str
    provider_id: str
    name: str
    address: str | None
    latitude: float
    longitude: float
    category: str
    duration_min: int
    score: float
    confirmed: bool
    model_config = {"from_attributes": True}


class EntityRead(BaseModel):
    id: UUID
    name: str
    category: str
    suggestion: str | None
    confidence: float
    candidates: list[CandidateRead]


class ContentRead(BaseModel):
    id: UUID
    trip_id: UUID
    content_type: str
    text: str
    status: str
    error: str | None
    created_at: datetime
    entities: list[EntityRead]


class ConfirmCandidates(BaseModel):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=20)
