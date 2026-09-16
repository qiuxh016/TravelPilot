from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from travelpilot_api.schemas.trips import TripCreate, TripRead, TripUpdate
from travelpilot_infra.db.session import get_session
from travelpilot_core.application.trips import TripService


router = APIRouter(
    prefix="/trips",
    tags=["trips"],
)


@router.post(
    "",
    response_model=TripRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_trip(
    payload: TripCreate,
    session: AsyncSession = Depends(get_session),
) -> TripRead:
    service = TripService(session)

    trip = await service.create_trip(
        destination=payload.destination,
        start_date=payload.start_date,
        end_date=payload.end_date,
        budget=payload.budget,
        pace_level=payload.pace_level,
    )

    return TripRead.model_validate(trip)


@router.get(
    "",
    response_model=list[TripRead],
)
async def list_trips(
    session: AsyncSession = Depends(get_session),
) -> list[TripRead]:
    service = TripService(session)
    trips = await service.list_trips()

    return [TripRead.model_validate(trip) for trip in trips]


@router.get(
    "/{trip_id}",
    response_model=TripRead,
)
async def get_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TripRead:
    service = TripService(session)
    trip = await service.get_trip(trip_id)

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
) -> TripRead:
    service = TripService(session)

    changes = payload.model_dump(exclude_unset=True)
    trip = await service.update_trip(trip_id, changes)

    if trip is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "TRIP_NOT_FOUND",
                    "message": "Trip does not exist",
                }
            },
        )

    return TripRead.model_validate(trip)


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    service = TripService(session)
    deleted = await service.delete_trip(trip_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )