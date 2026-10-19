"""Open-Meteo daily forecast client for non-commercial evaluation use."""

from datetime import UTC, date, datetime, timedelta
from time import monotonic

import httpx

WEATHER_CODES = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "有雾", 48: "冻雾", 51: "毛毛雨", 53: "中等毛毛雨", 55: "强毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "中等阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷雨", 96: "雷雨伴小冰雹", 99: "雷雨伴大冰雹",
}


class WeatherProviderError(RuntimeError):
    pass


class OpenMeteoProvider:
    source = "Open-Meteo"

    def __init__(self, timeout: float = 8, cache_seconds: int = 1800):
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple[str, str, str], tuple[float, dict]] = {}

    async def forecast(self, city: str, start_date: date, end_date: date) -> dict:
        cache_key = (city.strip().casefold(), start_date.isoformat(), end_date.isoformat())
        cached = self._cache.get(cache_key)
        if cached and cached[0] > monotonic():
            return cached[1]

        now = datetime.now(UTC)
        today = now.date()
        # Open-Meteo's forecast endpoint returns a rolling 16-day window.
        window_start, window_end = today, today + timedelta(days=15)
        if end_date < window_start or start_date > window_end:
            result = self._empty(city, start_date, end_date, now, "当前预报暂未覆盖这段行程日期。")
            self._cache[cache_key] = (monotonic() + self.cache_seconds, result)
            return result

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                geocode_response = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": city, "count": 1, "language": "zh", "format": "json"},
                )
                geocode_response.raise_for_status()
                matches = geocode_response.json().get("results", [])
                if not matches:
                    result = self._empty(city, start_date, end_date, now, "没有找到目的地坐标。")
                else:
                    location = matches[0]
                    response = await client.get(
                        "https://api.open-meteo.com/v1/forecast",
                        params={
                            "latitude": location["latitude"], "longitude": location["longitude"],
                            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                            "forecast_days": 16, "timezone": "auto",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    daily = data.get("daily", {})
                    days = []
                    for index, iso_day in enumerate(daily.get("time", [])):
                        day = date.fromisoformat(iso_day)
                        if start_date <= day <= end_date:
                            code = int(daily["weather_code"][index])
                            days.append({
                                "date": iso_day,
                                "description": WEATHER_CODES.get(code, "天气状况"),
                                "weather_code": code,
                                "temperature_max_c": daily["temperature_2m_max"][index],
                                "temperature_min_c": daily["temperature_2m_min"][index],
                                "precipitation_probability_max": daily["precipitation_probability_max"][index],
                            })
                    result = {
                        "status": "AVAILABLE" if days else "OUT_OF_RANGE",
                        "city": location.get("name", city),
                        "timezone": data.get("timezone"),
                        "source": "Open-Meteo",
                        "attribution": "Weather data by Open-Meteo (CC BY 4.0)",
                        "fetched_at": now,
                        "message": (
                            None if days and start_date >= window_start and end_date <= window_end
                            else "仅显示当前 16 天预报范围内的数据。"
                        ) if days else "当前预报暂未覆盖这段行程日期。",
                        "days": days,
                    }
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise WeatherProviderError("天气服务暂时不可用") from exc

        self._cache[cache_key] = (monotonic() + self.cache_seconds, result)
        return result

    @staticmethod
    def _empty(city: str, start_date: date, end_date: date, fetched_at: datetime, message: str) -> dict:
        return {
            "status": "OUT_OF_RANGE", "city": city, "timezone": None,
            "source": "Open-Meteo", "attribution": "Weather data by Open-Meteo (CC BY 4.0)",
            "fetched_at": fetched_at, "message": message, "days": [],
        }
