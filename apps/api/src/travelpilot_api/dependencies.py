from functools import lru_cache

from fastapi import HTTPException
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.maps.cached import CachedMapsProvider
from travelpilot_infra.providers.maps.errors import MapsConfigurationError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider

from travelpilot_api.settings import settings


@lru_cache(maxsize=1)
def get_maps_provider():
    try:
        provider = AmapProvider(settings.amap_api_key, settings.maps_timeout_seconds) if settings.maps_provider == "amap" else FakeMapsProvider()
    except MapsConfigurationError as exc:
        raise HTTPException(503, detail="地图服务未配置") from exc
    return CachedMapsProvider(provider)