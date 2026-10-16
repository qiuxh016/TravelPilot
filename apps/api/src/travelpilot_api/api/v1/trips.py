from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.trips import TripService
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_weather_provider
from travelpilot_api.schemas.trips import TripCreate, TripRead, TripUpdate
from travelpilot_api.schemas.weather import TripWeatherRead

router = APIRouter(
    prefix="/trips",
    tags=["trips"],
)


@router.get("/{trip_id}/weather", response_model=TripWeatherRead)
async def get_trip_weather(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    weather=Depends(get_weather_provider),
):
    trip = await session.scalar(select(TripModel).where(
        TripModel.id == trip_id, TripModel.owner_id == user.id,
    ))
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    try:
        return await weather.forecast(trip.destination, trip.start_date, trip.end_date)
    except WeatherProviderError as exc:
        raise HTTPException(status_code=503, detail="天气服务暂时不可用") from exc


@router.post(
    "",
    response_model=TripRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_trip(
    payload: TripCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> TripRead:
    service = TripService(session)

    trip = await service.create_trip(
        destination=payload.destination,
        start_date=payload.start_date,
        end_date=payload.end_date,
        budget=payload.budget,
        pace_level=payload.pace_level,
        owner_id=user.id,
    )

    return TripRead.model_validate(trip)


@router.get(
    "",
    response_model=list[TripRead],
)
async def list_trips(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> list[TripRead]:
    service = TripService(session)
    trips = await service.list_trips(user.id)

    return [TripRead.model_validate(trip) for trip in trips]


@router.get(
    "/{trip_id}",
    response_model=TripRead,
)
async def get_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> TripRead:
    service = TripService(session)
    trip = await service.get_trip(trip_id, user.id)

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    return TripRead.model_validate(trip)


@router.patch(
    "/{trip_id}",
    response_model=TripRead,
)
async def update_trip(
    trip_id: UUID,
    payload: TripUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> TripRead:
    service = TripService(session)

    changes = payload.model_dump(exclude_unset=True)
    trip = await service.update_trip(trip_id, user.id, changes)

    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    return TripRead.model_validate(trip)


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> None:
    service = TripService(session)
    deleted = await service.delete_trip(trip_id, user.id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )
