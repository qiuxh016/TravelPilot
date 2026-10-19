"""Deterministic local weather data for offline development and evaluation."""

from datetime import UTC, date, datetime, timedelta


class FakeWeatherProvider:
    source = "Local simulated forecast"

    async def forecast(self, city: str, start_date: date, end_date: date) -> dict:
        days = []
        current = start_date
        while current <= end_date:
            # Stable rainy-day fixtures exercise umbrella and indoor-plan guidance.
            raining = current.toordinal() % 2 == 0
            code = 61 if raining else 2
            days.append({
                "date": current.isoformat(),
                "description": "小雨" if raining else "局部多云",
                "weather_code": code,
                "temperature_max_c": 21.0,
                "temperature_min_c": 14.0,
                "precipitation_probability_max": 70 if raining else 20,
            })
            current += timedelta(days=1)
        now = datetime.now(UTC)
        return {
            "status": "AVAILABLE" if days else "OUT_OF_RANGE",
            "city": city,
            "timezone": None,
            "source": self.source,
            "attribution": "本地模拟数据，仅用于开发与评测",
            "fetched_at": now,
            "message": "这是本地模拟天气，不代表实际预报。",
            "days": days,
        }
