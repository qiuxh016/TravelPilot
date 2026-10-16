from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class WeatherDayRead(BaseModel):
    date: date
    description: str
    weather_code: int
    temperature_max_c: float | None
    temperature_min_c: float | None
    precipitation_probability_max: int | None


class TripWeatherRead(BaseModel):
    status: Literal["AVAILABLE", "OUT_OF_RANGE"]
    city: str
    timezone: str | None
    source: str
    attribution: str
    fetched_at: datetime
    message: str | None
    days: list[WeatherDayRead]
