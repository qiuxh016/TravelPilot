from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelpilot_infra.db.models.trip import TripModel


class TripRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, trip: TripModel) -> TripModel:
        self.session.add(trip)
        await self.session.flush()
        await self.session.refresh(trip)
        return trip

    async def get_by_id(self, trip_id: UUID) -> TripModel | None:
        result = await self.session.execute(
            select(TripModel).where(TripModel.id == trip_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[TripModel]:
        result = await self.session.execute(
            select(TripModel).order_by(TripModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(
        self,
        trip: TripModel,
        changes: dict,
    ) -> TripModel:
        for field, value in changes.items():
            setattr(trip, field, value)

        await self.session.flush()
        await self.session.refresh(trip)
        return trip

    async def delete(self, trip: TripModel) -> None:
        await self.session.delete(trip)
        await self.session.flush()