from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from travelpilot_api.schemas.wishlist import (
    WishlistCreate,
    WishlistUpdate,
)
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel


class WishlistService:
    """Wishlist 业务服务。

    负责：
    - 检查 Trip 是否存在
    - 创建 POI
    - 创建 WishlistItem
    - 查询 Wishlist
    - 修改收藏信息
    - 删除收藏
    - 提交数据库事务
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_item(
        self,
        trip_id: UUID,
        payload: WishlistCreate,
    ) -> tuple[WishlistItemModel, PoiModel]:
        """为指定 Trip 创建一个 WishlistItem。"""

        # 1. 检查 Trip 是否存在
        trip = await self.session.get(TripModel, trip_id)

        if trip is None:
            raise ValueError("Trip not found")

        # 2. 创建 POI
        poi = PoiModel(
            name=payload.name,
            category=payload.category,
            address=payload.address,
            latitude=payload.latitude,
            longitude=payload.longitude,
            suggested_duration_min=payload.suggested_duration_min,
        )

        self.session.add(poi)

        # 3. 将 POI 写入当前事务，使 poi.id 可用
        await self.session.flush()

        # 此时 poi.id 已经生成
        if poi.id is None:
            raise RuntimeError("POI id was not generated")

        # 4. 创建 WishlistItem
        item = WishlistItemModel(
            trip_id=trip_id,
            poi_id=poi.id,
            priority=payload.priority,
            interest_score=payload.interest_score,
            notes=payload.notes,
            source_type=payload.source_type,
            source_url=payload.source_url,
        )

        self.session.add(item)

        # 5. 将 WishlistItem 写入当前事务
        await self.session.flush()

        # 6. 提交整个事务
        await self.session.commit()

        # 7. 刷新对象，确保拿到数据库中的最终值
        await self.session.refresh(poi)
        await self.session.refresh(item)

        return item, poi

    async def list_items(
        self,
        trip_id: UUID,
    ) -> list[tuple[WishlistItemModel, PoiModel]]:
        """查询某个 Trip 的全部 Wishlist。"""

        # 先检查 Trip 是否存在
        trip = await self.session.get(TripModel, trip_id)

        if trip is None:
            raise ValueError("Trip not found")

        result = await self.session.execute(
            select(WishlistItemModel, PoiModel)
            .join(
                PoiModel,
                WishlistItemModel.poi_id == PoiModel.id,
            )
            .where(WishlistItemModel.trip_id == trip_id)
            .order_by(
                WishlistItemModel.priority.asc(),
                WishlistItemModel.created_at.asc(),
            )
        )

        return list(result.all())

    async def get_item(
        self,
        item_id: UUID,
    ) -> tuple[WishlistItemModel, PoiModel] | None:
        """根据 WishlistItem ID 查询收藏及其 POI。"""

        result = await self.session.execute(
            select(WishlistItemModel, PoiModel)
            .join(
                PoiModel,
                WishlistItemModel.poi_id == PoiModel.id,
            )
            .where(WishlistItemModel.id == item_id)
        )

        row = result.one_or_none()

        if row is None:
            return None

        item, poi = row
        return item, poi

    async def update_item(
        self,
        item_id: UUID,
        payload: WishlistUpdate,
    ) -> tuple[WishlistItemModel, PoiModel] | None:
        """修改收藏的优先级、兴趣分数和备注。"""

        row = await self.get_item(item_id)

        if row is None:
            return None

        item, poi = row

        changes = payload.model_dump(
            exclude_unset=True,
        )

        for field, value in changes.items():
            setattr(item, field, value)

        await self.session.flush()
        await self.session.commit()

        await self.session.refresh(item)
        await self.session.refresh(poi)

        return item, poi

    async def delete_item(
        self,
        item_id: UUID,
    ) -> bool:
        """删除 WishlistItem。

        第一版只删除收藏关系，不删除 POI。
        这样同一个 POI 将来可以被其他旅行继续使用。
        """

        item = await self.session.get(
            WishlistItemModel,
            item_id,
        )

        if item is None:
            return False

        await self.session.delete(item)
        await self.session.commit()

        return True