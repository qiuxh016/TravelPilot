"""Ten minute query cache for local API processes."""

import time


class CachedMapsProvider:
    def __init__(self, provider, ttl_seconds: int = 600):
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self._entries: dict[tuple, tuple[float, object]] = {}

    async def _get(self, method: str, *args, **kwargs):
        key = (type(self.provider).__name__, method, args, tuple(sorted(kwargs.items())))
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry and entry[0] > now:
            return entry[1]
        value = await getattr(self.provider, method)(*args, **kwargs)
        self._entries[key] = (now + self.ttl_seconds, value)
        return value

    async def search_place(self, keyword: str, city: str | None = None):
        return await self._get("search_place", keyword.strip(), (city or "").strip())

    async def get_place_detail(self, provider_id: str):
        return await self._get("get_place_detail", provider_id)

    async def get_route(self, origin, destination, mode="DRIVING", *, origin_city_code=None, destination_city_code=None):
        if mode == "DRIVING" and not origin_city_code and not destination_city_code:
            args = (tuple(origin), tuple(destination))
        elif mode == "WALKING" and not origin_city_code and not destination_city_code:
            args = (tuple(origin), tuple(destination), mode)
        elif mode == "TRANSIT":
            args = (tuple(origin), tuple(destination), mode)
            return await self._get(
                "get_route", *args,
                origin_city_code=origin_city_code,
                destination_city_code=destination_city_code,
            )
        else:
            args = (tuple(origin), tuple(destination), mode, origin_city_code, destination_city_code)
        return await self._get("get_route", *args)

    async def get_distance_matrix(self, points):
        return await self._get("get_distance_matrix", tuple(tuple(point) for point in points))

    async def get_static_map(self, points, selected_index=0, route_path=None):
        return await self.provider.get_static_map(tuple(tuple(point) for point in points), selected_index, route_path)
