from functools import lru_cache
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import ContentSourceModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.amap import AmapProvider
from travelpilot_infra.providers.maps.cached import CachedMapsProvider
from travelpilot_infra.providers.maps.errors import MapsConfigurationError
from travelpilot_infra.providers.maps.fake import FakeMapsProvider
from travelpilot_infra.providers.weather.open_meteo import OpenMeteoProvider

from travelpilot_api.auth import get_current_user
from travelpilot_api.settings import settings


async def get_owned_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> TripModel:
    trip = await session.scalar(select(TripModel).where(
        TripModel.id == trip_id, TripModel.owner_id == user.id,
    ))
    if trip is None:
        raise HTTPException(404, detail="Trip not found")
    return trip


async def get_owned_wishlist_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> WishlistItemModel:
    item = await session.scalar(select(WishlistItemModel).join(TripModel).where(
        WishlistItemModel.id == item_id, TripModel.owner_id == user.id,
    ))
    if item is None:
        raise HTTPException(404, detail="Wishlist item not found")
    return item


async def get_owned_content(
    content_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
) -> ContentSourceModel:
    source = await session.scalar(select(ContentSourceModel).join(TripModel).where(
        ContentSourceModel.id == content_id, TripModel.owner_id == user.id,
    ))
    if source is None:
        raise HTTPException(404, detail="Content not found")
    return source


@lru_cache(maxsize=1)
def get_maps_provider():
    try:
        provider = AmapProvider(settings.amap_api_key, settings.maps_timeout_seconds) if settings.maps_provider == "amap" else FakeMapsProvider()
    except MapsConfigurationError as exc:
        raise HTTPException(503, detail="地图服务未配置") from exc
    return CachedMapsProvider(provider)


@lru_cache(maxsize=1)
def get_weather_provider():
    return OpenMeteoProvider(settings.weather_timeout_seconds)
