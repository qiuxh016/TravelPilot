from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.trip_activity import record_trip_activity
from travelpilot_core.application.trips import TripService, find_duplicate_trip
from travelpilot_infra.db.models.plan import TripPlanModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_weather_provider
from travelpilot_api.schemas.trips import TripCreate, TripRead, TripUpdate
from travelpilot_api.schemas.weather import TripWeatherRead
from travelpilot_api.trip_access import get_trip_access

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
    access = await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    trip = access.trip
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

    try:
        trip = await service.create_trip(
            destination=payload.destination,
            start_date=payload.start_date,
            end_date=payload.end_date,
            budget=payload.budget,
            pace_level=payload.pace_level,
            owner_id=user.id,
        )
    except ValueError as exc:
        if str(exc) == "DUPLICATE_TRIP":
            raise HTTPException(status_code=409, detail="已存在相同目的地和日期的旅行。请切换到已有旅行继续规划。") from exc
        raise

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
    trip_ids = [trip.id for trip in trips]
    planned_trip_ids = set((await session.scalars(select(TripPlanModel.trip_id).where(
        TripPlanModel.trip_id.in_(trip_ids)
    ).distinct())).all()) if trip_ids else set()
    return [TripRead.model_validate(trip).model_copy(update={"has_plan": trip.id in planned_trip_ids}) for trip in trips]


@router.get(
    "/{trip_id}",
    response_model=TripRead,
)
async def get_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> TripRead:
    access = await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    return TripRead.model_validate(access.trip).model_copy(update={"access_role": access.role})


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
    access = await get_trip_access(
        session, trip_id=trip_id, user_id=user.id, required_role="EDITOR", lock=True,
    )
    changes = payload.model_dump(exclude_unset=True)
    trip = access.trip
    destination = changes.get("destination", trip.destination)
    start_date = changes.get("start_date", trip.start_date)
    end_date = changes.get("end_date", trip.end_date)
    if await find_duplicate_trip(
        session, trip.owner_id, destination, start_date, end_date, exclude_trip_id=trip.id,
    ):
        raise HTTPException(status_code=409, detail="已有一段目的地和日期相同的旅行，不能保存为重复记录。")
    for field, value in changes.items():
        setattr(trip, field, value)
    record_trip_activity(
        session, trip_id=trip_id, actor_user_id=user.id,
        actor_display_name=user.display_name or user.username,
        event_type="TRIP_UPDATED", summary="更新了旅行基本信息",
        payload={"changed_fields": sorted(changes)},
    )
    await session.commit()
    await session.refresh(trip)
    return TripRead.model_validate(trip).model_copy(update={"access_role": access.role})


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> None:
    access = await get_trip_access(
        session, trip_id=trip_id, user_id=user.id, required_role="OWNER",
    )
    service = TripService(session)
    deleted = await service.delete_trip(trip_id, access.trip.owner_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )
