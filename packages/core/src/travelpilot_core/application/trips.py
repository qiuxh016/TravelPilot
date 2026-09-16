from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.repositories.trips import TripRepository


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
    ) -> TripModel:
        trip = TripModel(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            budget=budget,
            pace_level=pace_level,
            status="DRAFT",
        )

        created = await self.repository.create(trip)
        await self.session.commit()
        return created

    async def get_trip(self, trip_id: UUID) -> TripModel | None:
        return await self.repository.get_by_id(trip_id)

    async def list_trips(self) -> list[TripModel]:
        return await self.repository.list_all()

    async def update_trip(
        self,
        trip_id: UUID,
        changes: dict,
    ) -> TripModel | None:
        trip = await self.repository.get_by_id(trip_id)

        if trip is None:
            return None

        updated = await self.repository.update(trip, changes)
        await self.session.commit()
        return updated

    async def delete_trip(self, trip_id: UUID) -> bool:
        trip = await self.repository.get_by_id(trip_id)

        if trip is None:
            return False

        await self.repository.delete(trip)
        await self.session.commit()
        return True