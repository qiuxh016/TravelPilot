import math

import httpx
from travelpilot_core.ports.maps import Place, Route

from .errors import MapsConfigurationError, MapsRetryableError, MapsUpstreamError


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
        except httpx.TimeoutException as exc:
            raise MapsRetryableError("地图服务请求超时") from exc
        except httpx.TransportError as exc:
            raise MapsRetryableError("地图服务暂时不可用") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 or exc.response.status_code >= 500:
                raise MapsRetryableError("地图服务暂时不可用") from exc
            raise MapsUpstreamError("地图服务返回错误") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MapsUpstreamError("地图服务暂时不可用") from exc
        if data.get("status") != "1":
            raise MapsUpstreamError("地图服务返回错误")
        return data

    def _place(self, item):
        longitude, latitude = (float(value) for value in item["location"].split(","))
        return Place("amap", item["id"], item["name"], item.get("address"), item.get("type") or "CUSTOM", latitude, longitude, raw_metadata=item)

    async def search_place(self, keyword, city=None):
        data = await self._get("/place/text", keywords=keyword, city=city or "", offset=20)
        return [self._place(item) for item in data.get("pois", []) if item.get("location")]

    async def get_place_detail(self, provider_id):
        data = await self._get("/place/detail", id=provider_id)
        return self._place(data["pois"][0])

    async def get_route(self, origin, destination):
        data = await self._get("/direction/driving", origin=f"{origin[1]},{origin[0]}", destination=f"{destination[1]},{destination[0]}")
        path = data["route"]["paths"][0]
        coordinates = []
        for step in path.get("steps", []):
            for pair in step.get("polyline", "").split(";"):
                try:
                    longitude, latitude = (float(value) for value in pair.split(",", 1))
                except (ValueError, TypeError):
                    continue
                point = (latitude, longitude)
                if not coordinates or coordinates[-1] != point:
                    coordinates.append(point)
        return Route(origin, destination, int(path["distance"]), max(1, round(int(path["duration"]) / 60)), "amap", tuple(coordinates))

    async def get_distance_matrix(self, points):
        if not points:
            return []
        origins = "|".join(f"{point[1]},{point[0]}" for point in points)
        columns = []
        for point in points:
            data = await self._get("/distance", origins=origins,
                                   destination=f"{point[1]},{point[0]}", type=1)
            results = data.get("results", [])
            if len(results) != len(points):
                raise MapsUpstreamError("地图服务暂时不可用")
            try:
                columns.append([max(0, round(int(item["duration"]) / 60)) for item in results])
            except (KeyError, TypeError, ValueError) as exc:
                raise MapsUpstreamError("地图服务暂时不可用") from exc
        return [[columns[destination][origin] for destination in range(len(points))]
                for origin in range(len(points))]

    async def get_static_map(self, points, selected_index=0, route_path=None):
        """Return a server-fetched AMap static map so the Web Service key stays private."""
        if not points:
            raise ValueError("至少需要一个地图点")
        locations = [(longitude, latitude) for latitude, longitude in points]
        center = (
            sum(point[0] for point in locations) / len(locations),
            sum(point[1] for point in locations) / len(locations),
        )
        longitude_span = max((point[0] for point in locations), default=center[0]) - min((point[0] for point in locations), default=center[0])
        latitude_span = max((point[1] for point in locations), default=center[1]) - min((point[1] for point in locations), default=center[1])
        # Fit the full set to the 1024x420 viewport instead of clipping distant stops
        # at a fixed city-block zoom. A single/nearby stop set gets a close city view.
        fit_width_zoom = math.log2(1440 / max(longitude_span, 0.001))
        fit_height_zoom = math.log2(590 / max(latitude_span, 0.001))
        zoom = max(4, min(17, math.floor(min(fit_width_zoom, fit_height_zoom))))
        markers = "|".join(
            f"mid,{('0xC17D37' if index == selected_index else '0x236443')},{label}:{longitude:.6f},{latitude:.6f}"
            for index, (label, (longitude, latitude)) in enumerate(zip("123456789A", locations, strict=False))
        )
        params = {
            "location": f"{center[0]:.6f},{center[1]:.6f}",
            "zoom": str(zoom),
            "size": "1024*420",
            "markers": markers,
        }
        if route_path and len(route_path) > 1:
            path = ";".join(f"{longitude:.6f},{latitude:.6f}" for latitude, longitude in route_path)
            params["paths"] = f"6,0x236443,0.9,,:{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get("https://restapi.amap.com/v3/staticmap", params={**params, "key": self.api_key})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MapsUpstreamError("地图服务暂时不可用") from exc
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise MapsUpstreamError("地图服务返回错误")
        return response.content, content_type
