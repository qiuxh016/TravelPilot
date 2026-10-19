"""Validation for live and recovered forecast evidence."""

import math
from datetime import date, datetime
from typing import Any

from travelpilot_infra.providers.weather.open_meteo import WEATHER_CODES

_DAY_FIELDS = {
    "date", "description", "weather_code", "temperature_max_c",
    "temperature_min_c", "precipitation_probability_max",
}


def valid_weather_evidence(
    value: Any, *, expected_source: str = "Open-Meteo", require_city: bool = True,
) -> bool:
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    if status not in {"AVAILABLE", "OUT_OF_RANGE"}:
        return False
    if value.get("source") != expected_source:
        return False
    if require_city and (
        not isinstance(value.get("city"), str)
        or not value["city"].strip()
        or len(value["city"]) > 120
    ):
        return False
    fetched_at = value.get("fetched_at")
    if not isinstance(fetched_at, (str, datetime)):
        return False
    try:
        fetched = fetched_at if isinstance(fetched_at, datetime) else datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        return False

    days = value.get("days")
    if not isinstance(days, list) or len(days) > 16:
        return False
    if (status == "AVAILABLE" and not days) or (status == "OUT_OF_RANGE" and days):
        return False

    seen_dates: set[date] = set()
    previous_date: date | None = None
    for item in days:
        if not isinstance(item, dict) or set(item) - _DAY_FIELDS:
            return False
        raw_date = item.get("date")
        description = item.get("description")
        if not isinstance(raw_date, str) or not isinstance(description, str) or not description.strip() or len(description) > 80:
            return False
        try:
            forecast_date = date.fromisoformat(raw_date)
        except ValueError:
            return False
        if raw_date != forecast_date.isoformat() or forecast_date in seen_dates:
            return False
        if previous_date is not None and forecast_date <= previous_date:
            return False
        seen_dates.add(forecast_date)
        previous_date = forecast_date

        code = item.get("weather_code")
        if code is not None and (
            isinstance(code, bool) or not isinstance(code, int) or code not in WEATHER_CODES
        ):
            return False
        numeric_ranges = {
            "temperature_max_c": (-100, 70),
            "temperature_min_c": (-100, 70),
            "precipitation_probability_max": (0, 100),
        }
        for field, (minimum, maximum) in numeric_ranges.items():
            number = item.get(field)
            if number is not None and (
                isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not minimum <= number <= maximum
            ):
                return False
        low, high = item.get("temperature_min_c"), item.get("temperature_max_c")
        if low is not None and high is not None and low > high:
            return False
    return True
