"""Exercise configured AMap and Open-Meteo adapters with bounded public inputs."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from travelpilot_api.settings import settings
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.weather.open_meteo import OpenMeteoProvider

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "tests/evaluation/results/live_provider_smoke_20261019.json"


def _failure(exc: Exception) -> dict[str, str]:
    return {"status": "FAILED", "error_type": type(exc).__name__}


async def run_smoke(*, output: Path) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = datetime.now().astimezone().date() + timedelta(days=1)
    end = start + timedelta(days=1)
    report: dict[str, Any] = {
        "run_at_utc": now.isoformat(),
        "maps_provider": "AMap Web Service",
        "weather_provider": "Open-Meteo",
        "test_scope": "Two public Chengdu POI searches, one route, one 2x2 distance matrix, and a two-day forecast.",
        "maps": {"status": "NOT_RUN"},
        "weather": {"status": "NOT_RUN"},
        "external_request_counts": {"amap_search": 0, "amap_route": 0, "amap_matrix": 0, "open_meteo": "at most 2 HTTP requests"},
    }

    places = []
    if not settings.amap_api_key:
        report["maps"] = {"status": "SKIPPED", "reason": "AMAP_API_KEY_NOT_CONFIGURED"}
    else:
        maps = AmapProvider(settings.amap_api_key, timeout=settings.maps_timeout_seconds)
        searches = []
        for keyword in ("宽窄巷子", "成都博物馆"):
            report["external_request_counts"]["amap_search"] += 1
            try:
                found = await maps.search_place(keyword, city="成都")
                searches.append({
                    "keyword": keyword,
                    "status": "SUCCEEDED",
                    "result_count": len(found),
                    "sample_names": [place.name[:80] for place in found[:3]],
                })
                if found:
                    places.append(found[0])
            except Exception as exc:  # noqa: BLE001 -- record only type, never provider text
                searches.append({"keyword": keyword, **_failure(exc)})

        route = {"status": "SKIPPED", "reason": "TWO_VERIFIED_POIS_REQUIRED"}
        matrix = {"status": "SKIPPED", "reason": "TWO_VERIFIED_POIS_REQUIRED"}
        if len(places) >= 2:
            start_point = (places[0].latitude, places[0].longitude)
            end_point = (places[1].latitude, places[1].longitude)
            report["external_request_counts"]["amap_route"] += 1
            try:
                value = await maps.get_route(start_point, end_point, mode="DRIVING")
                route = {
                    "status": "SUCCEEDED",
                    "source": value.provider,
                    "distance_m": value.distance_m,
                    "duration_min": value.duration_min,
                    "geometry_point_count": len(value.polyline),
                    "valid": (
                        value.provider == "amap"
                        and value.distance_m >= 0
                        and value.duration_min > 0
                        and len(value.polyline) >= 2
                    ),
                }
            except Exception as exc:  # noqa: BLE001 -- record only type
                route = _failure(exc)

            report["external_request_counts"]["amap_matrix"] += 2
            try:
                durations = await maps.get_distance_matrix([start_point, end_point])
                matrix = {
                    "status": "SUCCEEDED",
                    "shape": [len(durations), *(len(row) for row in durations)],
                    "diagonal_zero": (
                        len(durations) == 2
                        and all(len(row) == 2 for row in durations)
                        and durations[0][0] == 0
                        and durations[1][1] == 0
                    ),
                    "durations_min": durations,
                }
            except Exception as exc:  # noqa: BLE001 -- record only type
                matrix = _failure(exc)
        report["maps"] = {"status": "SUCCEEDED" if searches else "FAILED", "searches": searches,
                          "route": route, "distance_matrix": matrix}

    weather = OpenMeteoProvider(timeout=settings.weather_timeout_seconds)
    try:
        result = await weather.forecast("成都", start, end)
        days = result.get("days", [])
        report["weather"] = {
            "status": result.get("status"),
            "source": result.get("source"),
            "attribution": result.get("attribution"),
            "forecast_dates_requested": [start.isoformat(), end.isoformat()],
            "forecast_day_count": len(days),
            "sample_days": [
                {key: day.get(key) for key in (
                    "date", "description", "temperature_min_c", "temperature_max_c",
                    "precipitation_probability_max",
                )}
                for day in days
            ],
            "valid": (
                result.get("source") == "Open-Meteo"
                and result.get("status") in {"AVAILABLE", "OUT_OF_RANGE"}
                and isinstance(result.get("fetched_at"), datetime)
            ),
        }
    except Exception as exc:  # noqa: BLE001 -- record only type
        report["weather"] = _failure(exc)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = asyncio.run(run_smoke(output=args.output))
    summary = {
        "maps_status": result["maps"].get("status"),
        "weather_status": result["weather"].get("status"),
        "external_request_counts": result["external_request_counts"],
        "report": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
