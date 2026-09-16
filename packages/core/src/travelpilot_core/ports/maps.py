from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Place:
    provider: str
    provider_id: str
    name: str
    address: str | None
    category: str
    latitude: float
    longitude: float
    opening_hours: dict | None = None
    suggested_duration_min: int = 60
    raw_metadata: dict | None = None

@dataclass(frozen=True)
class Route:
    origin: tuple[float, float]
    destination: tuple[float, float]
    distance_m: int
    duration_min: int
    provider: str

class MapsPort(Protocol):
    async def search_place(self, keyword: str, city: str | None = None) -> list[Place]: ...
    async def get_place_detail(self, provider_id: str) -> Place: ...
    async def get_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> Route: ...
    async def get_distance_matrix(self, points: list[tuple[float, float]]) -> list[list[int]]: ...