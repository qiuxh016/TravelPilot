import httpx
from travelpilot_core.ports.maps import Place, Route
from .errors import MapsConfigurationError, MapsUpstreamError

class AmapProvider:
    def __init__(self, api_key: str, timeout: float = 8):
        if not api_key:
            raise MapsConfigurationError("AMAP_API_KEY 未配置")
        self.api_key, self.timeout = api_key, timeout
        self.base_url = "https://restapi.amap.com/v3"

    async def _get(self, path: str, **params):
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.base_url + path, params={**params, "key": self.api_key, "output": "json"})
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MapsUpstreamError("地图服务暂时不可用") from exc
        if data.get("status") != "1":
            raise MapsUpstreamError("地图服务返回错误")
        return data

    def _place(self, item):
        longitude, latitude = (float(value) for value in item["location"].split(","))
        return Place("amap", item["id"], item["name"], item.get("address"), "CUSTOM", latitude, longitude, raw_metadata=item)

    async def search_place(self, keyword, city=None):
        data = await self._get("/place/text", keywords=keyword, city=city or "", offset=20)
        return [self._place(item) for item in data.get("pois", []) if item.get("location")]

    async def get_place_detail(self, provider_id):
        data = await self._get("/place/detail", id=provider_id)
        return self._place(data["pois"][0])

    async def get_route(self, origin, destination):
        data = await self._get("/direction/driving", origin=f"{origin[1]},{origin[0]}", destination=f"{destination[1]},{destination[0]}")
        path = data["route"]["paths"][0]
        return Route(origin, destination, int(path["distance"]), max(1, round(int(path["duration"]) / 60)), "amap")

    async def get_distance_matrix(self, points):
        origins = ";".join(f"{point[1]},{point[0]}" for point in points)
        data = await self._get("/distance", origins=origins, destination=f"{points[0][1]},{points[0][0]}")
        return [[int(item["duration"]) // 60 for item in data.get("results", [])]]
