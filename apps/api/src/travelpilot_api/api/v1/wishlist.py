from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.plans import PlanService
from travelpilot_core.application.wishlist import WishlistService
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.dependencies import get_owned_trip, get_owned_wishlist_item
from travelpilot_api.schemas.environment import user_profile
from travelpilot_api.schemas.wishlist import (
    WishlistCreate,
    WishlistRead,
    WishlistUpdate,
)

# 创建和查询某个旅行下的 Wishlist
trip_wishlist_router = APIRouter(
    prefix="/trips/{trip_id}/wishlist",
    tags=["wishlist"],
)


# 修改和删除单个 WishlistItem
wishlist_item_router = APIRouter(
    prefix="/wishlist",
    tags=["wishlist"],
)


def to_wishlist_read(item, poi) -> WishlistRead:
    """将数据库模型转换为 API 响应模型。"""

    return WishlistRead(
        id=item.id,
        trip_id=item.trip_id,
        poi_id=item.poi_id,
        name=poi.name,
        category=poi.category,
        address=poi.address,
        latitude=poi.latitude,
        longitude=poi.longitude,
        suggested_duration_min=poi.suggested_duration_min,
        priority=item.priority,
        interest_score=item.interest_score,
        notes=item.notes,
        source_type=item.source_type,
        source_url=item.source_url,
        environment_profile=item.environment_profile or poi.environment_profile,
    )


@trip_wishlist_router.post(
    "",
    response_model=WishlistRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_wishlist_item(
    trip_id: UUID,
    payload: WishlistCreate,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
) -> WishlistRead:
    """为指定 Trip 添加一个 Wishlist 地点。"""

    service = WishlistService(session)

    try:
        item, poi = await service.create_item(
            trip_id=trip_id,
            payload=payload,
            environment_profile=user_profile(payload.environment_profile),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    return to_wishlist_read(item, poi)


@trip_wishlist_router.get(
    "",
    response_model=list[WishlistRead],
)
async def list_wishlist_items(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
) -> list[WishlistRead]:
    """查询指定 Trip 的全部 Wishlist。"""

    service = WishlistService(session)

    try:
        rows = await service.list_items(trip_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    return [
        to_wishlist_read(item, poi)
        for item, poi in rows
    ]


@wishlist_item_router.patch(
    "/{item_id}",
    response_model=WishlistRead,
)
async def update_wishlist_item(
    item_id: UUID,
    payload: WishlistUpdate,
    session: AsyncSession = Depends(get_session),
    _item: WishlistItemModel = Depends(get_owned_wishlist_item),
) -> WishlistRead:
    """修改 Wishlist 的优先级、兴趣分数和备注。"""

    service = WishlistService(session)

    result = await service.update_item(
        item_id=item_id,
        payload=payload,
        environment_profile=user_profile(payload.environment_profile),
        update_environment_profile="environment_profile" in payload.model_fields_set,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wishlist item not found",
        )

    item, poi = result

    return to_wishlist_read(item, poi)


@wishlist_item_router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_wishlist_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
    _item: WishlistItemModel = Depends(get_owned_wishlist_item),
) -> None:
    """删除 WishlistItem，但保留 POI。"""

    service = WishlistService(session)

    try:
        await PlanService(session).remove_poi_from_latest(_item.trip_id, _item.poi_id)
        deleted = await service.delete_item(item_id, commit=False)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wishlist item not found")
        await session.commit()
    except Exception:
        await session.rollback()
        raise
