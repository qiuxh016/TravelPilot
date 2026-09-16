from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class WishlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="CUSTOM", max_length=50)
    address: str | None = Field(default=None, max_length=300)
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    suggested_duration_min: int = Field(default=60, ge=1)
    priority: int = Field(default=3, ge=1, le=5)
    interest_score: Decimal | None = Field(default=None, ge=0, le=1)
    notes: str | None = None
    source_type: str = Field(default="MANUAL", max_length=50)
    source_url: str | None = None


class WishlistUpdate(BaseModel):
    priority: int | None = Field(default=None, ge=1, le=5)
    interest_score: Decimal | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class WishlistRead(BaseModel):
    id: UUID
    trip_id: UUID
    poi_id: UUID
    name: str
    category: str
    address: str | None
    latitude: Decimal
    longitude: Decimal
    suggested_duration_min: int
    priority: int
    interest_score: Decimal | None
    notes: str | None
    source_type: str
    source_url: str | None

    model_config = {
        "from_attributes": True,
    }