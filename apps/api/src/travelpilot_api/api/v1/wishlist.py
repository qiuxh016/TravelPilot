from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.plans import PlanService
from travelpilot_core.application.trip_activity import record_trip_activity
from travelpilot_core.application.wishlist import WishlistService
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.environment import user_profile
from travelpilot_api.schemas.wishlist import (
    WishlistCreate,
    WishlistRead,
    WishlistUpdate,
)
from travelpilot_api.trip_access import get_trip_access

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


async def _wishlist_item_access(session: AsyncSession, item_id: UUID, user_id: UUID,
                                role: str, lock: bool = False):
    item = await session.scalar(select(WishlistItemModel).where(
        WishlistItemModel.id == item_id,
    ).with_for_update() if lock else select(WishlistItemModel).where(WishlistItemModel.id == item_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    access = await get_trip_access(
        session, trip_id=item.trip_id, user_id=user_id,
        required_role=role, lock=lock,
    )
    return item, access


@trip_wishlist_router.post(
    "",
    response_model=WishlistRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_wishlist_item(
    trip_id: UUID,
    payload: WishlistCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> WishlistRead:
    """为指定 Trip 添加一个 Wishlist 地点。"""

    await get_trip_access(session, trip_id=trip_id, user_id=user.id, required_role="EDITOR", lock=True)
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

    record_trip_activity(
        session, trip_id=trip_id, actor_user_id=user.id,
        actor_display_name=user.display_name or user.username,
        event_type="WISHLIST_PLACE_ADDED", summary=f"将 {poi.name} 加入共同收藏",
        payload={"poi_id": str(poi.id)},
    )
    await session.commit()

    return to_wishlist_read(item, poi)


@trip_wishlist_router.get(
    "",
    response_model=list[WishlistRead],
)
async def list_wishlist_items(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> list[WishlistRead]:
    """查询指定 Trip 的全部 Wishlist。"""

    await get_trip_access(session, trip_id=trip_id, user_id=user.id)
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
    user: UserModel = Depends(get_current_user),
) -> WishlistRead:
    """修改 Wishlist 的优先级、兴趣分数和备注。"""

    _item, _access = await _wishlist_item_access(session, item_id, user.id, "EDITOR", lock=True)
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
    record_trip_activity(
        session, trip_id=item.trip_id, actor_user_id=user.id,
        actor_display_name=user.display_name or user.username,
        event_type="WISHLIST_PLACE_UPDATED", summary=f"更新了 {poi.name} 的共同收藏信息",
        payload={"poi_id": str(poi.id)},
    )
    await session.commit()

    return to_wishlist_read(item, poi)


@wishlist_item_router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_wishlist_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> None:
    """删除 WishlistItem，但保留 POI。"""

    _item, _access = await _wishlist_item_access(session, item_id, user.id, "EDITOR", lock=True)
    service = WishlistService(session)

    try:
        await PlanService(session).remove_poi_from_latest(
            _item.trip_id, _item.poi_id, user.id, user.display_name or user.username,
        )
        deleted = await service.delete_item(item_id, commit=False)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wishlist item not found")
        record_trip_activity(
            session, trip_id=_item.trip_id, actor_user_id=user.id,
            actor_display_name=user.display_name or user.username,
            event_type="WISHLIST_PLACE_REMOVED", summary="从共同收藏中移除了一个地点",
            payload={"poi_id": str(_item.poi_id)},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
