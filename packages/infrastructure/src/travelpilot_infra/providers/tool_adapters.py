"""Native map and weather tool adapters; MCP may implement the same schemas."""

from dataclasses import asdict

from travelpilot_core.ports.tool_schemas import (
    GetDistanceMatrixInput,
    GetPlaceDetailInput,
    GetRouteInput,
    GetWeatherInput,
    SearchPlacesInput,
)
from travelpilot_core.ports.tools import ToolSpec


def map_tool_specs(maps) -> list[ToolSpec]:
    async def search(args: SearchPlacesInput):
        return {"places": [asdict(place) for place in await maps.search_place(args.keyword, args.city)]}

    async def detail(args: GetPlaceDetailInput):
        return asdict(await maps.get_place_detail(args.provider_id))

    async def route(args: GetRouteInput):
        return asdict(await maps.get_route(args.origin, args.destination))

    async def matrix(args: GetDistanceMatrixInput):
        return {"durations_min": await maps.get_distance_matrix(args.points)}

    async def weather(args: GetWeatherInput):
        # Weather is explicitly unavailable until a provider is configured.
        return {"status": "UNAVAILABLE", "city": args.city, "day": args.day}

    return [
        ToolSpec("search_places", SearchPlacesInput, search),
        ToolSpec("get_place_detail", GetPlaceDetailInput, detail),
        ToolSpec("get_route", GetRouteInput, route),
        ToolSpec("get_weather", GetWeatherInput, weather),
        ToolSpec("get_distance_matrix", GetDistanceMatrixInput, matrix),
    ]
