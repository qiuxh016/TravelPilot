from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.social import FriendshipModel, TripShareGrantModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user, normalize_email
from travelpilot_api.schemas.social import (
    FriendRequestCreate,
    FriendRequestNotice,
    FriendshipRead,
    SharedTripDetailRead,
    SharedTripPlanNodeRead,
    SharedTripRead,
    TripShareCreate,
    TripShareRead,
)

router = APIRouter(tags=["friends and trip sharing"])


def _pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    return (left, right) if left.int < right.int else (right, left)


async def _accepted_friendship(session: AsyncSession, user_id: UUID, friend_id: UUID):
    low, high = _pair(user_id, friend_id)
    return await session.scalar(select(FriendshipModel).where(
        FriendshipModel.user_low_id == low,
        FriendshipModel.user_high_id == high,
        FriendshipModel.status == "ACCEPTED",
    ))


def _friend_read(row: FriendshipModel, actor_id: UUID, other: UserModel) -> FriendshipRead:
    direction = "OUTGOING" if row.status == "PENDING" and row.requested_by_id == actor_id else (
        "INCOMING" if row.status == "PENDING" else "NONE"
    )
    return FriendshipRead(
        id=row.id, friend_user_id=other.id, email=other.email, status=row.status,
        direction=direction, created_at=row.created_at,
    )


@router.post("/friends/requests", response_model=FriendRequestNotice, status_code=202)
async def request_friend(
    payload: FriendRequestCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    email = normalize_email(payload.email)
    other = await session.scalar(select(UserModel).where(
        UserModel.email == email, UserModel.is_active.is_(True), UserModel.email_verified.is_(True)
    ))
    message = "如果该邮箱对应可添加的账户，我们已处理好友申请。"
    if other is None or other.id == user.id:
        return FriendRequestNotice(message=message)
    low, high = _pair(user.id, other.id)
    friendship = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.user_low_id == low, FriendshipModel.user_high_id == high
    ))
    if friendship is None:
        friendship = FriendshipModel(
            user_low_id=low, user_high_id=high, requested_by_id=user.id, status="PENDING"
        )
        session.add(friendship)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
    elif friendship.status == "REJECTED":
        friendship.status = "PENDING"
        friendship.requested_by_id = user.id
        friendship.blocked_by_id = None
        friendship.updated_at = datetime.now(UTC)
        await session.commit()
    return FriendRequestNotice(message=message)


@router.get("/friends", response_model=list[FriendshipRead])
async def list_friends(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "ACCEPTED")
        .order_by(UserModel.email)
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.get("/friends/requests", response_model=list[FriendshipRead])
async def list_friend_requests(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "PENDING")
        .order_by(FriendshipModel.created_at.desc())
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.get("/friends/blocked", response_model=list[FriendshipRead])
async def list_blocked_friends(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(FriendshipModel, UserModel)
        .join(UserModel, or_(
            and_(FriendshipModel.user_low_id == user.id, UserModel.id == FriendshipModel.user_high_id),
            and_(FriendshipModel.user_high_id == user.id, UserModel.id == FriendshipModel.user_low_id),
        ))
        .where(or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
               FriendshipModel.status == "BLOCKED", FriendshipModel.blocked_by_id == user.id)
        .order_by(UserModel.email)
    )).all()
    return [_friend_read(row, user.id, other) for row, other in rows]


@router.post("/friends/requests/{friendship_id}/accept", response_model=FriendshipRead)
async def accept_friend_request(
    friendship_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "PENDING",
        FriendshipModel.requested_by_id != user.id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    row.status = "ACCEPTED"
    row.updated_at = datetime.now(UTC)
    other_id = row.user_high_id if row.user_low_id == user.id else row.user_low_id
    other = await session.get(UserModel, other_id)
    await session.commit()
    return _friend_read(row, user.id, other)


@router.delete("/friends/requests/{friendship_id}", status_code=204)
async def decline_friend_request(
    friendship_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "PENDING",
        FriendshipModel.requested_by_id != user.id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    await session.delete(row)
    await session.commit()


async def _end_friendship(friendship_id: UUID, user: UserModel, session: AsyncSession, block: bool):
    conditions = [
        FriendshipModel.id == friendship_id,
        or_(FriendshipModel.user_low_id == user.id, FriendshipModel.user_high_id == user.id),
        FriendshipModel.status.in_(["PENDING", "ACCEPTED"] if block else ["ACCEPTED"]),
    ]
    row = await session.scalar(select(FriendshipModel).where(*conditions))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    other_id = row.user_high_id if row.user_low_id == user.id else row.user_low_id
    if block:
        row.status = "BLOCKED"
        row.blocked_by_id = user.id
        row.updated_at = datetime.now(UTC)
    else:
        await session.delete(row)
    await session.execute(update(TripShareGrantModel).where(
        or_(
            and_(TripShareGrantModel.owner_user_id == user.id, TripShareGrantModel.grantee_user_id == other_id),
            and_(TripShareGrantModel.owner_user_id == other_id, TripShareGrantModel.grantee_user_id == user.id),
        ),
        TripShareGrantModel.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(UTC)))
    await session.commit()


@router.delete("/friends/{friendship_id}", status_code=204)
async def remove_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    await _end_friendship(friendship_id, user, session, block=False)


@router.post("/friends/{friendship_id}/block", status_code=204)
async def block_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    await _end_friendship(friendship_id, user, session, block=True)


@router.delete("/friends/{friendship_id}/block", status_code=204)
async def unblock_friend(friendship_id: UUID, session: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    row = await session.scalar(select(FriendshipModel).where(
        FriendshipModel.id == friendship_id, FriendshipModel.status == "BLOCKED",
        FriendshipModel.blocked_by_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    await session.delete(row)
    await session.commit()


@router.get("/trips/{trip_id}/shares", response_model=list[TripShareRead])
async def list_trip_shares(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    trip = await session.scalar(select(TripModel).where(TripModel.id == trip_id, TripModel.owner_id == user.id))
    if trip is None:
        raise HTTPException(404, detail="Resource not found")
    rows = (await session.execute(
        select(TripShareGrantModel, UserModel).join(UserModel, UserModel.id == TripShareGrantModel.grantee_user_id)
        .where(TripShareGrantModel.trip_id == trip_id, TripShareGrantModel.revoked_at.is_(None))
        .order_by(TripShareGrantModel.created_at.desc())
    )).all()
    return [TripShareRead(
        id=grant.id, trip_id=grant.trip_id, grantee_user_id=grant.grantee_user_id,
        email=friend.email, scope=grant.scope, created_at=grant.created_at,
    ) for grant, friend in rows]


@router.post("/trips/{trip_id}/shares", response_model=TripShareRead, status_code=201)
async def create_trip_share(
    trip_id: UUID,
    payload: TripShareCreate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    if payload.scope not in {"TRIP_SUMMARY", "TRIP_AND_PLAN"} or payload.friend_user_id == user.id:
        raise HTTPException(422, detail="Invalid share settings")
    trip = await session.scalar(select(TripModel).where(TripModel.id == trip_id, TripModel.owner_id == user.id))
    if trip is None or await _accepted_friendship(session, user.id, payload.friend_user_id) is None:
        raise HTTPException(404, detail="Resource not found")
    friend = await session.get(UserModel, payload.friend_user_id)
    if friend is None or not friend.is_active:
        raise HTTPException(404, detail="Resource not found")
    grant = await session.scalar(select(TripShareGrantModel).where(
        TripShareGrantModel.trip_id == trip_id,
        TripShareGrantModel.grantee_user_id == friend.id,
    ))
    if grant is None:
        grant = TripShareGrantModel(
            trip_id=trip_id, owner_user_id=user.id, grantee_user_id=friend.id, scope=payload.scope
        )
        session.add(grant)
    else:
        grant.owner_user_id = user.id
        grant.scope = payload.scope
        grant.revoked_at = None
    await session.commit()
    await session.refresh(grant)
    return TripShareRead(
        id=grant.id, trip_id=trip_id, grantee_user_id=friend.id, email=friend.email,
        scope=grant.scope, created_at=grant.created_at,
    )


@router.delete("/trips/{trip_id}/shares/{grant_id}", status_code=204)
async def revoke_trip_share(
    trip_id: UUID,
    grant_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    grant = await session.scalar(select(TripShareGrantModel).join(
        TripModel, TripModel.id == TripShareGrantModel.trip_id
    ).where(
        TripShareGrantModel.id == grant_id,
        TripShareGrantModel.trip_id == trip_id,
        TripShareGrantModel.revoked_at.is_(None),
        TripModel.owner_id == user.id,
    ))
    if grant is None:
        raise HTTPException(404, detail="Resource not found")
    grant.revoked_at = datetime.now(UTC)
    await session.commit()


@router.get("/shared/trips", response_model=list[SharedTripRead])
async def list_shared_trips(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(TripModel, TripShareGrantModel, UserModel)
        .join(TripShareGrantModel, TripShareGrantModel.trip_id == TripModel.id)
        .join(UserModel, UserModel.id == TripModel.owner_id)
        .join(FriendshipModel, and_(
            FriendshipModel.user_low_id == func.least(user.id, TripModel.owner_id),
            FriendshipModel.user_high_id == func.greatest(user.id, TripModel.owner_id),
            FriendshipModel.status == "ACCEPTED",
        ))
        .where(TripShareGrantModel.grantee_user_id == user.id,
               TripShareGrantModel.owner_user_id == TripModel.owner_id,
               TripShareGrantModel.revoked_at.is_(None))
        .order_by(TripModel.start_date)
    )).all()
    return [SharedTripRead(
        id=trip.id, destination=trip.destination, start_date=trip.start_date,
        end_date=trip.end_date, scope=grant.scope, owner_email=owner.email,
    ) for trip, grant, owner in rows]


@router.get("/shared/trips/{trip_id}", response_model=SharedTripDetailRead)
async def get_shared_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = (await session.execute(
        select(TripModel, TripShareGrantModel, UserModel)
        .join(TripShareGrantModel, TripShareGrantModel.trip_id == TripModel.id)
        .join(UserModel, UserModel.id == TripModel.owner_id)
        .join(FriendshipModel, and_(
            FriendshipModel.user_low_id == func.least(user.id, TripModel.owner_id),
            FriendshipModel.user_high_id == func.greatest(user.id, TripModel.owner_id),
            FriendshipModel.status == "ACCEPTED",
        ))
        .where(TripModel.id == trip_id, TripShareGrantModel.grantee_user_id == user.id,
               TripShareGrantModel.owner_user_id == TripModel.owner_id,
               TripShareGrantModel.revoked_at.is_(None))
    )).first()
    if row is None:
        raise HTTPException(404, detail="Resource not found")
    trip, grant, owner = row
    latest_version = None
    nodes: list[SharedTripPlanNodeRead] = []
    if grant.scope == "TRIP_AND_PLAN":
        latest_version = await session.scalar(select(func.max(TripPlanModel.version)).where(TripPlanModel.trip_id == trip_id))
        if latest_version is not None:
            plan_id = await session.scalar(select(TripPlanModel.id).where(
                TripPlanModel.trip_id == trip_id, TripPlanModel.version == latest_version
            ))
            plan_nodes = (await session.execute(
                select(TripPlanNodeModel, PoiModel.name)
                .join(PoiModel, PoiModel.id == TripPlanNodeModel.poi_id)
                .where(TripPlanNodeModel.plan_id == plan_id)
                .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
            )).all()
            nodes = [SharedTripPlanNodeRead(
                name=name, day=node.day, start_time=node.start_time.isoformat(),
                end_time=node.end_time.isoformat(), status=node.status,
            ) for node, name in plan_nodes]
    return SharedTripDetailRead(
        id=trip.id, destination=trip.destination, start_date=trip.start_date,
        end_date=trip.end_date, scope=grant.scope, owner_email=owner.email,
        plan_version=latest_version, plan_nodes=nodes,
    )
