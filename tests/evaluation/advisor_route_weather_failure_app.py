"""Test-only app combining a direct-route outage with local forecast data."""

from travelpilot_api.dependencies import get_maps_provider, get_weather_provider
from travelpilot_infra.providers.maps.errors import MapsRetryableError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider

from tests.evaluation.local_advisor_weather_app import (
    LocalEvaluationWeatherProvider,
    app,
)


class FakeDirectRouteOutageMaps(FakeMapsProvider):
    async def get_route(
        self,
        origin,
        destination,
        mode="DRIVING",
        *,
        origin_city_code=None,
        destination_city_code=None,
    ):
        raise MapsRetryableError("synthetic direct route timeout")


def _maps_provider():
    return FakeDirectRouteOutageMaps()


app.dependency_overrides[get_maps_provider] = _maps_provider
app.dependency_overrides[get_weather_provider] = LocalEvaluationWeatherProvider
