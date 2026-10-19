"""Advisor API app with a local AMap-shaped fixture for free-city discovery."""

from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.main import app
from travelpilot_core.ports.maps import Place


class AmapProvider:
    """Deterministic local fixture; this class never performs network calls."""

    async def search_place(self, keyword: str, city: str | None = None) -> list[Place]:
        city = city or "杭州"
        if keyword in {"公园", "自然"}:
            name, category, latitude, longitude = f"{city}西湖公园", "公园;风景名胜", 30.24, 120.15
        elif keyword in {"美食", "餐饮"}:
            name, category, latitude, longitude = f"{city}河坊街美食", "餐饮服务;特色小吃", 30.25, 120.17
        else:
            name, category, latitude, longitude = f"{city}{keyword}体验点", "风景名胜;旅游景点", 30.26, 120.16
        return [Place(
            provider="amap",
            provider_id=f"local-{city}-{keyword}",
            name=name,
            address=f"{city}本地评测区域",
            category=category,
            latitude=latitude,
            longitude=longitude,
            raw_metadata={"biz_ext": {"rating": "4.8"}},
            city_code="330100",
        )]


app.dependency_overrides[get_maps_provider] = lambda: AmapProvider()
