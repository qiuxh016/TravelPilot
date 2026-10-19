"""Test-only app combining a forecast outage with Fake Maps route evidence."""

from travelpilot_api.dependencies import get_weather_provider
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from tests.evaluation.local_advisor_weather_app import app


class LocalWeatherOutageProvider:
    source = "Local synthetic weather outage"

    async def forecast(self, _city, _start_date, _end_date):
        raise WeatherProviderError("synthetic forecast timeout")


app.dependency_overrides[get_weather_provider] = LocalWeatherOutageProvider
