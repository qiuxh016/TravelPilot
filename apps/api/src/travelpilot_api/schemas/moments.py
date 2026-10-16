from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MomentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    trip_id: UUID | None = None
    day: date | None = None
    place_name: str | None = Field(default=None, max_length=160)
    audience: Literal["PRIVATE", "FRIENDS"] = "PRIVATE"
    memory_extraction_enabled: bool = False

    @field_validator("body")
    @classmethod
    def non_blank_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("旅行感受不能为空")
        return value


class MomentUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    audience: Literal["PRIVATE", "FRIENDS"] | None = None
    day: date | None = None
    place_name: str | None = Field(default=None, max_length=160)
    memory_extraction_enabled: bool | None = None

    @field_validator("body")
    @classmethod
    def non_blank_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("旅行感受不能为空")
        return value


class MomentMediaRead(BaseModel):
    id: UUID
    mime_type: str
    size_bytes: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MomentRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    author_email: str
    body: str
    audience: str
    memory_extraction_enabled: bool
    day: date | None
    place_name: str | None
    trip_id: UUID | None = None
    created_at: datetime
    media: list[MomentMediaRead] = Field(default_factory=list)
    reaction_count: int = 0
    reacted_by_me: bool = False
    comment_count: int = 0


class MomentCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def non_blank_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("评论不能为空")
        return value


class MomentCommentRead(BaseModel):
    id: UUID
    author_user_id: UUID
    author_email: str
    body: str
    created_at: datetime


class MemorySuggestionRead(BaseModel):
    id: UUID
    candidate: str
    evidence: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MemorySuggestionUpdate(BaseModel):
    candidate: str = Field(min_length=2, max_length=2000)
    accepted: bool

    @field_validator("candidate")
    @classmethod
    def non_blank_candidate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("记忆内容不能为空")
        return value
