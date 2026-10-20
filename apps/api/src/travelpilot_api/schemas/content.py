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
    evidence: str | None = None
    confidence: float
    candidates: list[CandidateRead]


class GuideFactRead(BaseModel):
    id: UUID
    kind: str
    title: str
    content: str
    evidence: str | None = None
    related_places: list[str] = Field(default_factory=list)
    confidence: float


class ContentRead(BaseModel):
    id: UUID
    trip_id: UUID
    content_type: str
    text: str
    status: str
    error: str | None
    skill_name: str | None = None
    skill_version: str | None = None
    skill_applicability: dict | None = None
    token_usage: int = 0
    guide_facts: list[GuideFactRead] = Field(default_factory=list)
    created_at: datetime
    entities: list[EntityRead]


class ConfirmCandidates(BaseModel):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=20)
    fact_ids: list[UUID] = Field(default_factory=list, max_length=40)
