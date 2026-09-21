"""Ten minute query cache for local API processes."""

import time


class CachedMapsProvider:
    def __init__(self, provider, ttl_seconds: int = 600):
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self._entries: dict[tuple, tuple[float, object]] = {}

    async def _get(self, method: str, *args):
        key = (type(self.provider).__name__, method, args)
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry and entry[0] > now:
            return entry[1]
        value = await getattr(self.provider, method)(*args)
        self._entries[key] = (now + self.ttl_seconds, value)
        return value

    async def search_place(self, keyword: str, city: str | None = None):
        return await self._get("search_place", keyword.strip(), (city or "").strip())

    async def get_place_detail(self, provider_id: str):
        return await self._get("get_place_detail", provider_id)

    async def get_route(self, origin, destination):
        return await self._get("get_route", tuple(origin), tuple(destination))

    async def get_distance_matrix(self, points):
        return await self._get("get_distance_matrix", tuple(tuple(point) for point in points))
