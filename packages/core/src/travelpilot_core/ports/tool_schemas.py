"""Input schemas for the five public agent tools and planner matrix lookup."""

from uuid import UUID

from pydantic import BaseModel, Field


class SearchPlacesInput(BaseModel):
    keyword: str = Field(min_length=1, max_length=100)
    city: str | None = Field(default=None, max_length=100)


class GetPlaceDetailInput(BaseModel):
    provider_id: str = Field(min_length=1, max_length=200)


class GetRouteInput(BaseModel):
    origin: tuple[float, float]
    destination: tuple[float, float]


class GetWeatherInput(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    day: str = Field(min_length=10, max_length=10)


class GetTripContextInput(BaseModel):
    trip_id: UUID


class GetDistanceMatrixInput(BaseModel):
    points: list[tuple[float, float]] = Field(min_length=2, max_length=30)
