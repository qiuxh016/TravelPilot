from travelpilot_api.settings import settings
from travelpilot_infra.providers.maps.fake import FakeMapsProvider
from travelpilot_infra.providers.maps.amap import AmapProvider
def get_maps_provider():
    return AmapProvider(settings.amap_api_key, settings.maps_timeout_seconds) if settings.maps_provider == "amap" else FakeMapsProvider()