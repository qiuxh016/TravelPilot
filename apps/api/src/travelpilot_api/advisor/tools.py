"""Advisor's allowlisted map tools and provider evidence adapters."""

from datetime import UTC, date, datetime

from pydantic import BaseModel
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tool_schemas import GetDistanceMatrixInput, SearchPlacesInput
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.settings import settings


async def _empty_trace(_: dict) -> None:
    return None


class ForecastInput(BaseModel):
    """No model-controlled fields: forecast scope comes from the selected trip."""


def advisor_tool_runtime(maps, *, weather=None, city=None, start_date=None, end_date=None) -> AgentRuntime:
    tools = [ToolSpec("search_places", SearchPlacesInput, _search_handler(maps),
                      timeout_seconds=settings.maps_timeout_seconds, retry_limit=0)]
    if weather is not None and city and isinstance(start_date, date) and isinstance(end_date, date):
        async def forecast(_: ForecastInput) -> dict:
            try:
                result = await weather.forecast(city, start_date, end_date)
            except WeatherProviderError as exc:
                raise ToolFailure("WEATHER_PROVIDER_UNAVAILABLE") from exc
            return {**result, "fetched_at": result["fetched_at"].isoformat()}

        tools.append(ToolSpec(
            "get_trip_weather", ForecastInput, forecast,
            timeout_seconds=settings.weather_timeout_seconds, retry_limit=0,
        ))
    return AgentRuntime(tools, _empty_trace, max_steps=25)


def _search_handler(maps):
    async def search(arguments: SearchPlacesInput) -> dict:
        try:
            places = await maps.search_place(arguments.keyword, arguments.city)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        status = (
            "SIMULATED" if any(place.provider == "fake" for place in places)
            else "VERIFIED" if places else "NO_RESULTS"
        )
        return {
            "status": status,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "places": [{
                "provider": place.provider, "provider_id": place.provider_id,
                "name": place.name, "address": place.address, "category": place.category,
                "latitude": place.latitude, "longitude": place.longitude,
                "city_code": place.city_code,
                "duration_min": place.suggested_duration_min, "opening_hours": place.opening_hours,
                "popularity_score": map_popularity_score(place.raw_metadata),
            } for place in places],
        }

    return search


def map_popularity_score(metadata: dict | None) -> float | None:
    """Use provider supplied rating/importance without inventing popularity."""
    if not isinstance(metadata, dict):
        return None
    business = metadata.get("biz_ext")
    value = business.get("rating") if isinstance(business, dict) else None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if 0 <= score <= 10 else None


def rank_map_places(places: list[dict]) -> list[dict]:
    """Prefer provider-rated places within a result card; never infer missing scores."""
    return sorted(
        places,
        key=lambda place: (
            place.get("popularity_score") is None,
            -(place.get("popularity_score") or 0),
            place.get("name", "").casefold(),
        ),
    )


def map_evidence_source(maps) -> str:
    provider = getattr(maps, "provider", maps)
    provider_name = type(provider).__name__
    if provider_name.casefold().startswith("fake"):
        return "Local simulated maps"
    return "AMap Web Service" if provider_name == "AmapProvider" else provider_name


def search_tool_runtime(maps) -> AgentRuntime:
    return advisor_tool_runtime(maps)


def distance_tool_runtime(maps) -> AgentRuntime:
    async def distance_matrix(arguments: GetDistanceMatrixInput) -> dict:
        try:
            durations = await maps.get_distance_matrix(arguments.points)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        source = map_evidence_source(maps)
        simulated = source == "Local simulated maps"
        return {
            "durations_min": durations,
            "status": "SIMULATED" if simulated else "VERIFIED",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    return AgentRuntime(
        [ToolSpec("get_distance_matrix", GetDistanceMatrixInput, distance_matrix,
                  timeout_seconds=settings.maps_timeout_seconds, retry_limit=0)],
        _empty_trace,
        max_steps=1,
    )
