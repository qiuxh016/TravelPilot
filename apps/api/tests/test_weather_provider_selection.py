from datetime import date

import pytest
from travelpilot_api import dependencies
from travelpilot_api.settings import settings
from travelpilot_infra.providers.weather.fake import FakeWeatherProvider
from travelpilot_infra.providers.weather.open_meteo import OpenMeteoProvider


def test_weather_provider_uses_local_fixture_when_explicitly_selected(monkeypatch):
    monkeypatch.setattr(settings, "weather_provider", "fake")
    dependencies.get_weather_provider.cache_clear()

    provider = dependencies.get_weather_provider()
    dependencies.get_weather_provider.cache_clear()

    assert isinstance(provider, FakeWeatherProvider)


def test_weather_provider_defaults_to_open_meteo(monkeypatch):
    monkeypatch.setattr(settings, "weather_provider", "open_meteo")
    dependencies.get_weather_provider.cache_clear()

    provider = dependencies.get_weather_provider()
    dependencies.get_weather_provider.cache_clear()

    assert isinstance(provider, OpenMeteoProvider)


@pytest.mark.asyncio
async def test_fake_weather_is_deterministic_and_covers_requested_dates():
    provider = FakeWeatherProvider()
    start = date(2026, 10, 22)

    first = await provider.forecast("成都", start, date(2026, 10, 23))
    second = await provider.forecast("成都", start, date(2026, 10, 23))

    assert first["status"] == "AVAILABLE"
    assert first["source"] == "Local simulated forecast"
    assert first["message"] == "这是本地模拟天气，不代表实际预报。"
    assert [day["date"] for day in first["days"]] == [
        "2026-10-22", "2026-10-23",
    ]
    assert [day["weather_code"] for day in first["days"]] == [
        day["weather_code"] for day in second["days"]
    ]
