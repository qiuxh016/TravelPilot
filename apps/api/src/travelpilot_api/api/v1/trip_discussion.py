import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.trip_collaboration import (
    TripActivityModel,
    TripDiscussionMessageModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.trip_member import TripMemberModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.trip_collaboration import (
    TripActivityRead,
    TripDiscussionCreate,
    TripDiscussionMessageRead,
)
from travelpilot_api.trip_access import get_trip_access

router = APIRouter(prefix="/group-trips/{trip_id}", tags=["trip collaboration"])


@router.get("/events")
async def stream_trip_discussion_events(
    trip_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    """Push new group discussion messages to currently authorized members."""
    await get_trip_access(session, trip_id=trip_id, user_id=user.id)

    async def events():
        latest = (await session.execute(
            select(TripDiscussionMessageModel.created_at, TripDiscussionMessageModel.id)
            .where(TripDiscussionMessageModel.trip_id == trip_id)
            .order_by(TripDiscussionMessageModel.created_at.desc(), TripDiscussionMessageModel.id.desc())
            .limit(1)
        )).first()
        cursor = (latest.created_at, latest.id) if latest else None
        yield f"event: snapshot\ndata: {json.dumps({'trip_id': str(trip_id)}, ensure_ascii=False)}\n\n"
        heartbeat = 0
        while not await request.is_disconnected():
            current_owner = await session.scalar(select(TripModel.owner_id).where(TripModel.id == trip_id))
            if current_owner is None:
                yield "event: access_revoked\ndata: {}\n\n"
                break
            if current_owner != user.id:
                membership_status = await session.scalar(select(TripMemberModel.status).where(
                    TripMemberModel.trip_id == trip_id,
                    TripMemberModel.user_id == user.id,
                ))
                if membership_status != "ACCEPTED":
                    yield "event: access_revoked\ndata: {}\n\n"
                    break

            statement = select(TripDiscussionMessageModel).where(
                TripDiscussionMessageModel.trip_id == trip_id
            )
            if cursor is not None:
                statement = statement.where(tuple_(
                    TripDiscussionMessageModel.created_at, TripDiscussionMessageModel.id,
                ) > tuple_(cursor[0], cursor[1]))
            rows = (await session.scalars(
                statement.order_by(TripDiscussionMessageModel.created_at, TripDiscussionMessageModel.id).limit(100)
            )).all()
            for message in rows:
                payload = {
                    "id": str(message.id),
                    "trip_id": str(message.trip_id),
                    "author_user_id": str(message.author_user_id) if message.author_user_id else None,
                    "author_display_name": message.author_display_name,
                    "body": message.body,
                    "created_at": message.created_at.isoformat(),
                }
                yield f"event: discussion_message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                cursor = (message.created_at, message.id)
                heartbeat = 0
            if not rows:
                heartbeat += 1
                if heartbeat >= 12:
                    yield ": keep-alive\n\n"
                    heartbeat = 0
            await asyncio.sleep(2)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
