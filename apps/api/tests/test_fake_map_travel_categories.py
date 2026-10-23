import pytest
from travelpilot_infra.providers.maps.fake import FakeMapsProvider


@pytest.mark.asyncio
async def test_fake_map_search_supports_food_and_lodging_without_mixing_domains():
    maps = FakeMapsProvider()

    food = await maps.search_place("餐饮服务", "成都")
    lodging = await maps.search_place("住宿服务", "成都")

    assert [place.provider_id for place in food] == ["fake-restaurant"]
    assert food[0].category.startswith("餐饮服务")
    assert [place.provider_id for place in lodging] == ["fake-hotel"]
    assert lodging[0].category.startswith("住宿服务")


@pytest.mark.asyncio
async def test_fake_map_still_supports_exact_attraction_name_search():
    places = await FakeMapsProvider().search_place("宽窄巷子", "成都")

    assert [place.provider_id for place in places] == ["fake-kuanzhai"]


@pytest.mark.asyncio
async def test_fake_map_supports_generic_attraction_category_search():
    places = await FakeMapsProvider().search_place("景点", "成都")

    assert {place.provider_id for place in places} == {"fake-panda", "fake-kuanzhai"}
    assert all("景点" in place.category for place in places)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DRIVING", "WALKING", "TRANSIT"])
async def test_fake_routes_include_geometry_required_by_route_evidence(mode):
    origin = (30.663, 104.055)
    destination = (30.646, 104.048)

    route = await FakeMapsProvider().get_route(origin, destination, mode)

    assert route.polyline == (origin, destination)
    assert route.mode == mode
