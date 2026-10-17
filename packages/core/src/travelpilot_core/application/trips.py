from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.repositories.trips import TripRepository


def normalize_destination(value: str) -> str:
    return " ".join(value.casefold().split())


async def find_duplicate_trip(
    session: AsyncSession,
    owner_id: UUID,
    destination: str,
    start_date: date,
    end_date: date,
    exclude_trip_id: UUID | None = None,
) -> TripModel | None:
    candidates = (await session.scalars(select(TripModel).where(
        TripModel.owner_id == owner_id,
        TripModel.start_date == start_date,
        TripModel.end_date == end_date,
    ).order_by(TripModel.created_at))).all()
    normalized = normalize_destination(destination)
    return next((trip for trip in candidates
                 if trip.id != exclude_trip_id and normalize_destination(trip.destination) == normalized), None)


async def lock_trip_owner(session: AsyncSession, owner_id: UUID) -> None:
    await session.scalar(select(UserModel.id).where(UserModel.id == owner_id).with_for_update())


class TripService:
    def __init__(self, session: AsyncSession):
        self.repository = TripRepository(session)
        self.session = session

    async def create_trip(
        self,
        destination: str,
        start_date: date,
        end_date: date,
        budget: Decimal | None,
        pace_level: int,
        owner_id: UUID,
    ) -> TripModel:
        await lock_trip_owner(self.session, owner_id)
        if await find_duplicate_trip(self.session, owner_id, destination, start_date, end_date):
            raise ValueError("DUPLICATE_TRIP")

        trip = TripModel(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            budget=budget,
            pace_level=pace_level,
            status="DRAFT",
            owner_id=owner_id,
        )

        created = await self.repository.create(trip)
        await self.session.commit()
        return created

    async def get_trip(self, trip_id: UUID, owner_id: UUID) -> TripModel | None:
        return await self.repository.get_by_id(trip_id, owner_id)

    async def list_trips(self, owner_id: UUID) -> list[TripModel]:
        return await self.repository.list_by_owner(owner_id)

    async def update_trip(
        self,
        trip_id: UUID,
        owner_id: UUID,
        changes: dict,
    ) -> TripModel | None:
        await lock_trip_owner(self.session, owner_id)
        trip = await self.repository.get_by_id(trip_id, owner_id)

        if trip is None:
            return None

        destination = changes.get("destination", trip.destination)
        start_date = changes.get("start_date", trip.start_date)
        end_date = changes.get("end_date", trip.end_date)
        if await find_duplicate_trip(self.session, owner_id, destination, start_date, end_date, exclude_trip_id=trip.id):
            raise ValueError("DUPLICATE_TRIP")

        updated = await self.repository.update(trip, changes)
        await self.session.commit()
        return updated

    async def delete_trip(self, trip_id: UUID, owner_id: UUID) -> bool:
        trip = await self.repository.get_by_id(trip_id, owner_id)

        if trip is None:
            return False

        await self.repository.delete(trip)
        await self.session.commit()
        return True
