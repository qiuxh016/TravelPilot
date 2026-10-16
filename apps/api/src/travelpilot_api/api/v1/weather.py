from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_weather_provider
from travelpilot_api.schemas.weather import TripWeatherRead

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/forecast", response_model=TripWeatherRead)
async def forecast(
    city: str = Query(min_length=1, max_length=100),
    start_date: date = Query(),
    end_date: date = Query(),
    _user=Depends(get_current_user),
    weather=Depends(get_weather_provider),
):
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="结束日期不能早于开始日期")
    if (end_date - start_date).days > 15:
        raise HTTPException(status_code=422, detail="一次最多查询 16 天天气")
    try:
        return await weather.forecast(city, start_date, end_date)
    except WeatherProviderError as exc:
        raise HTTPException(status_code=503, detail="天气服务暂时不可用") from exc
