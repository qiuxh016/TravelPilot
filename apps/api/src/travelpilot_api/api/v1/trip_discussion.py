from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip_collaboration import (
    TripActivityModel,
    TripDiscussionMessageModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.trip_collaboration import (
    TripActivityRead,
    TripDiscussionCreate,
    TripDiscussionMessageRead,
)
from travelpilot_api.trip_access import get_trip_access

router = APIRouter(prefix="/group-trips/{trip_id}", tags=["trip collaboration"])


@router.get("/discussion", response_model=list[TripDiscussionMessageRead])
async def list_trip_discussion(
    trip_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    rows = (await session.scalars(
        select(TripDiscussionMessageModel)
        .where(TripDiscussionMessageModel.trip_id == trip_id)
        .order_by(TripDiscussionMessageModel.created_at.desc(), TripDiscussionMessageModel.id.desc())
        .limit(limit)
    )).all()
    return list(reversed(rows))


@router.post("/discussion", response_model=TripDiscussionMessageRead, status_code=status.HTTP_201_CREATED)
async def send_trip_discussion_message(
    trip_id: UUID,
    payload: TripDiscussionCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await get_trip_access(session, trip_id=trip_id, user_id=user.id, lock=True)
    message = TripDiscussionMessageModel(
        trip_id=trip_id,
        author_user_id=user.id,
        author_display_name=user.display_name or user.username or "旅行成员",
        body=payload.body,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


@router.get("/activity", response_model=list[TripActivityRead])
async def list_trip_activity(
    trip_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    rows = (await session.scalars(
        select(TripActivityModel)
        .where(TripActivityModel.trip_id == trip_id)
        .order_by(TripActivityModel.created_at.desc(), TripActivityModel.id.desc())
        .limit(limit)
    )).all()
    return list(reversed(rows))
