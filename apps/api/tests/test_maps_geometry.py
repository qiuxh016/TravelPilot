from typing import ClassVar

import httpx
import pytest
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.maps.errors import MapsUpstreamError


@pytest.mark.asyncio
async def test_amap_driving_route_parses_step_polylines_to_latitude_longitude():
    provider = AmapProvider("test-key")

    async def fake_get(_path, **_params):
        return {"route": {"paths": [{
            "distance": "650", "duration": "480", "steps": [
                {"polyline": "104.060000,30.660000;104.061000,30.661000"},
                {"polyline": "104.061000,30.661000;104.062000,30.662000"},
            ],
        }]}}

    provider._get = fake_get
    route = await provider.get_route((30.66, 104.06), (30.662, 104.062))

    assert route.provider == "amap"
    assert route.distance_m == 650
    assert route.duration_min == 8
    assert route.polyline == ((30.66, 104.06), (30.661, 104.061), (30.662, 104.062))


@pytest.mark.asyncio
async def test_amap_static_map_omits_line_when_no_road_geometry_is_provided(monkeypatch):
    provider = AmapProvider("test-key")
    captured = {}

    class ImageResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "image/png"}
        content = b"png"

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, params):
            captured.update(params)
            return ImageResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    await provider.get_static_map([(30.66, 104.06), (30.67, 104.07)])

    assert "paths" not in captured


@pytest.mark.asyncio
async def test_amap_static_map_draws_only_the_provided_road_geometry(monkeypatch):
    provider = AmapProvider("test-key")
    captured = {}

    class ImageResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "image/png"}
        content = b"png"

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, params):
            captured.update(params)
            return ImageResponse()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    await provider.get_static_map(
        [(30.66, 104.06), (30.67, 104.07)],
        route_path=[(30.66, 104.06), (30.665, 104.066), (30.67, 104.07)],
    )

    assert captured["paths"].endswith("104.060000,30.660000;104.066000,30.665000;104.070000,30.670000")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"},
    {"status": "0", "info": "DAILY_QUERY_OVER_LIMIT", "infocode": "10003"},
])
async def test_amap_provider_errors_do_not_return_upstream_error_payloads(monkeypatch, payload):
    provider = AmapProvider("test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(MapsUpstreamError):
        await provider.search_place("宽窄巷子", "成都")


@pytest.mark.asyncio
async def test_amap_provider_returns_empty_search_without_fabricating_places(monkeypatch):
    provider = AmapProvider("test-key")

    async def fake_get(_path, **_params):
        return {"status": "1", "pois": []}

    provider._get = fake_get
    assert await provider.search_place("不存在的地点", "成都") == []


@pytest.mark.asyncio
async def test_amap_timeout_is_reported_as_upstream_failure(monkeypatch):
    provider = AmapProvider("test-key")

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("upstream timeout")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(MapsUpstreamError):
        await provider.search_place("宽窄巷子", "成都")
