"""Local Advisor API entrypoint with an explicitly labelled weather fixture."""

from datetime import UTC, datetime

from travelpilot_api.dependencies import get_weather_provider
from travelpilot_api.main import app


class LocalEvaluationWeatherProvider:
    source = "Local simulated forecast"

    async def forecast(self, city, start_date, end_date):
        return {
            "status": "AVAILABLE",
            "city": city,
            "timezone": "Asia/Shanghai",
            "source": self.source,
            "attribution": "Synthetic evaluation fixture; not a real forecast.",
            "fetched_at": datetime.now(UTC),
            "message": "本地评测模拟天气，仅用于验证工具顺序。",
            "days": [
                {
                    "date": current.isoformat(),
                    "description": "小雨（模拟）",
                    "weather_code": 61,
                    "temperature_max_c": 20,
                    "temperature_min_c": 13,
                    "precipitation_probability_max": 75,
                }
                for current in sorted({start_date, end_date})
            ],
        }


app.dependency_overrides[get_weather_provider] = LocalEvaluationWeatherProvider
