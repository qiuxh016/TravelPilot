"""Test-only FastAPI app that injects a deterministic route-matrix outage."""

from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.main import app
from travelpilot_infra.providers.maps.errors import MapsRetryableError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider


class FakeRouteOutageMaps(FakeMapsProvider):
    async def get_distance_matrix(self, points):
        raise MapsRetryableError("synthetic route matrix timeout")


def _route_outage_maps():
    return FakeRouteOutageMaps()


app.dependency_overrides[get_maps_provider] = _route_outage_maps
