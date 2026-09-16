from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel


class WishlistRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_poi(self, poi: PoiModel) -> PoiModel:
        self.session.add(poi)
        await self.session.flush()
        await self.session.refresh(poi)
        return poi

    async def create_item(
        self,
        item: WishlistItemModel,
    ) -> WishlistItemModel:
        self.session.add(item)
        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def list_by_trip(
        self,
        trip_id: UUID,
    ) -> list[tuple[WishlistItemModel, PoiModel]]:
        result = await self.session.execute(
            select(WishlistItemModel, PoiModel)
            .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
            .where(WishlistItemModel.trip_id == trip_id)
            .order_by(WishlistItemModel.priority.asc())
        )

        return list(result.all())

    async def get_item(
        self,
        item_id: UUID,
    ) -> WishlistItemModel | None:
        result = await self.session.execute(
            select(WishlistItemModel).where(
                WishlistItemModel.id == item_id
            )
        )
        return result.scalar_one_or_none()

    async def update_item(
        self,
        item: WishlistItemModel,
        changes: dict,
    ) -> WishlistItemModel:
        for field, value in changes.items():
            setattr(item, field, value)

        await self.session.flush()
        await self.session.refresh(item)
        return item

    async def delete_item(
        self,
        item: WishlistItemModel,
    ) -> None:
        await self.session.delete(item)
        await self.session.flush()