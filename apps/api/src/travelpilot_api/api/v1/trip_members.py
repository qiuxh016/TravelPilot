from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.social import FriendshipModel, TripShareGrantModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.trip_member import TripMemberModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session

from travelpilot_api.auth import get_current_user
from travelpilot_api.schemas.social import SharedTripPlanNodeRead
from travelpilot_api.schemas.trip_members import (
    CollaborativeTripDetailRead,
    CollaborativeTripRead,
    TripInvitationRead,
    TripMemberInvite,
    TripMemberRead,
    TripMemberRoleUpdate,
    TripMembershipNotice,
)
from travelpilot_api.trip_access import TripAccess, get_trip_access

router = APIRouter(tags=["trip collaboration"])


async def _accepted_friendship(session: AsyncSession, left: UUID, right: UUID) -> bool:
    low, high = (left, right) if left.int < right.int else (right, left)
    return bool(await session.scalar(select(FriendshipModel.id).where(
        FriendshipModel.user_low_id == low,
        FriendshipModel.user_high_id == high,
        FriendshipModel.status == "ACCEPTED",
    )))


async def _owner_access(session: AsyncSession, trip_id: UUID, user_id: UUID) -> TripAccess:
    access = await get_trip_access(
        session, trip_id=trip_id, user_id=user_id, required_role="OWNER",
    )
    return access


def _member_read(member: TripMemberModel, person: UserModel) -> TripMemberRead:
    return TripMemberRead(
        id=member.id,
        trip_id=member.trip_id,
        user_id=person.id,
        username=person.username,
        display_name=person.display_name,
        has_avatar=bool(person.avatar_thumbnail_key),
        role=member.role,
        status=member.status,
        invited_by_id=member.invited_by_id,
        accepted_at=member.accepted_at,
        created_at=member.created_at,
    )


@router.get("/trips/{trip_id}/members", response_model=list[TripMemberRead])
async def list_trip_members(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    access = await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    query = select(TripMemberModel, UserModel).join(
        UserModel, UserModel.id == TripMemberModel.user_id
    ).where(TripMemberModel.trip_id == trip_id)
    # Pending/declined/revoked invitation details are private to the owner and invitee.
    if access.role != "OWNER":
        query = query.where(TripMemberModel.status == "ACCEPTED")
    rows = (await session.execute(query.order_by(TripMemberModel.created_at))).all()
    return [_member_read(member, person) for member, person in rows]


@router.post(
    "/trips/{trip_id}/members",
    response_model=TripMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_trip_member(
    trip_id: UUID,
    payload: TripMemberInvite,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _owner_access(session, trip_id, user.id)
    if payload.user_id == user.id:
        raise HTTPException(status_code=422, detail="Trip owner is already a member")
    friend = await session.get(UserModel, payload.user_id)
    if friend is None or not friend.is_active or not friend.email_verified:
        raise HTTPException(status_code=404, detail="Friend not found")
    if not await _accepted_friendship(session, user.id, friend.id):
        raise HTTPException(status_code=404, detail="Friend not found")
    readonly_grant = await session.scalar(select(TripShareGrantModel.id).where(
        TripShareGrantModel.trip_id == trip_id,
        TripShareGrantModel.grantee_user_id == friend.id,
        TripShareGrantModel.revoked_at.is_(None),
    ))
    if readonly_grant is not None:
        raise HTTPException(
            status_code=409,
            detail="Revoke this friend's read-only share before inviting them as a group member",
        )

    member = await session.scalar(select(TripMemberModel).where(
        TripMemberModel.trip_id == trip_id,
        TripMemberModel.user_id == friend.id,
    ).with_for_update())
    now = datetime.now(UTC)
    if member is None:
        member = TripMemberModel(
            trip_id=trip_id,
            user_id=friend.id,
            invited_by_id=user.id,
            role=payload.role,
            status="PENDING",
        )
        session.add(member)
    elif member.status in {"PENDING", "ACCEPTED"}:
        raise HTTPException(status_code=409, detail="This friend already has an active invitation or membership")
    else:
        member.invited_by_id = user.id
        member.role = payload.role
        member.status = "PENDING"
        member.accepted_at = None
        member.created_at = now
        member.updated_at = now

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Invitation already exists") from exc
    await session.refresh(member)
    return _member_read(member, friend)


@router.patch("/trips/{trip_id}/members/{member_id}", response_model=TripMemberRead)
async def update_trip_member_role(
    trip_id: UUID,
    member_id: UUID,
    payload: TripMemberRoleUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    await _owner_access(session, trip_id, user.id)
    row = (await session.execute(
        select(TripMemberModel, UserModel)
        .join(UserModel, UserModel.id == TripMemberModel.user_id)
        .where(TripMemberModel.id == member_id, TripMemberModel.trip_id == trip_id)
        .with_for_update(of=TripMemberModel)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Trip member not found")
    member, person = row
    if member.status not in {"PENDING", "ACCEPTED"}:
        raise HTTPException(status_code=409, detail="Inactive invitation cannot be changed")
    member.role = payload.role
    member.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(member)
    return _member_read(member, person)


@router.delete("/trips/{trip_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_or_leave_trip(
    trip_id: UUID,
    member_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    trip = await session.scalar(select(TripModel).where(TripModel.id == trip_id))
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    member = await session.scalar(select(TripMemberModel).where(
        TripMemberModel.id == member_id,
        TripMemberModel.trip_id == trip_id,
        TripMemberModel.status.in_(("PENDING", "ACCEPTED")),
    ).with_for_update())
    if member is None:
        raise HTTPException(status_code=404, detail="Trip member not found")
    if trip.owner_id != user.id and member.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trip member not found")
    member.status = "REVOKED"
    member.updated_at = datetime.now(UTC)
    await session.commit()


@router.get("/trip-invitations", response_model=list[TripInvitationRead])
async def list_pending_trip_invitations(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(TripMemberModel, UserModel.display_name)
        .join(UserModel, UserModel.id == TripMemberModel.invited_by_id)
        .where(
            TripMemberModel.user_id == user.id,
            TripMemberModel.status == "PENDING",
        )
        .order_by(TripMemberModel.created_at.desc())
    )).all()
    # Do not reveal the trip destination or itinerary before the recipient accepts.
    return [TripInvitationRead(
        id=member.id,
        inviter_display_name=inviter_name,
        role=member.role,
        created_at=member.created_at,
    ) for member, inviter_name in rows]


@router.get("/group-trips", response_model=list[CollaborativeTripRead])
async def list_collaborative_trips(
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    rows = (await session.execute(
        select(TripModel, TripMemberModel, UserModel)
        .join(TripMemberModel, TripMemberModel.trip_id == TripModel.id)
        .join(UserModel, UserModel.id == TripModel.owner_id)
        .where(
            TripMemberModel.user_id == user.id,
            TripMemberModel.status == "ACCEPTED",
        )
        .order_by(TripModel.start_date)
    )).all()
    return [CollaborativeTripRead(
        id=trip.id,
        destination=trip.destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        owner_display_name=owner.display_name or owner.username,
        role=member.role,
    ) for trip, member, owner in rows]


@router.get("/group-trips/{trip_id}", response_model=CollaborativeTripDetailRead)
async def get_collaborative_trip(
    trip_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    access = await get_trip_access(session, trip_id=trip_id, user_id=user.id)
    if access.membership is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    owner = await session.get(UserModel, access.trip.owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    latest_version = await session.scalar(select(func.max(TripPlanModel.version)).where(
        TripPlanModel.trip_id == trip_id
    ))
    nodes: list[SharedTripPlanNodeRead] = []
    if latest_version is not None:
        plan_id = await session.scalar(select(TripPlanModel.id).where(
            TripPlanModel.trip_id == trip_id,
            TripPlanModel.version == latest_version,
        ))
        rows = (await session.execute(
            select(TripPlanNodeModel, PoiModel.name)
            .join(PoiModel, PoiModel.id == TripPlanNodeModel.poi_id)
            .where(TripPlanNodeModel.plan_id == plan_id)
            .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
        )).all()
        nodes = [SharedTripPlanNodeRead(
            name=name,
            day=node.day,
            start_time=node.start_time.isoformat(),
            end_time=node.end_time.isoformat(),
            status=node.status,
        ) for node, name in rows]

    return CollaborativeTripDetailRead(
        id=access.trip.id,
        destination=access.trip.destination,
        start_date=access.trip.start_date,
        end_date=access.trip.end_date,
        owner_display_name=owner.display_name or owner.username,
        role=access.membership.role,
        plan_version=latest_version,
        plan_nodes=nodes,
    )


async def _respond_to_invitation(
    *,
    member_id: UUID,
    user: UserModel,
    session: AsyncSession,
    accepted: bool,
) -> TripMembershipNotice:
    member = await session.scalar(select(TripMemberModel).where(
        TripMemberModel.id == member_id,
        TripMemberModel.user_id == user.id,
        TripMemberModel.status == "PENDING",
    ).with_for_update())
    if member is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if accepted and (
        member.invited_by_id is None
        or not await _accepted_friendship(session, member.invited_by_id, user.id)
    ):
        raise HTTPException(status_code=409, detail="Friendship is no longer active")
    now = datetime.now(UTC)
    member.status = "ACCEPTED" if accepted else "DECLINED"
    member.accepted_at = now if accepted else None
    member.updated_at = now
    await session.commit()
    return TripMembershipNotice(message="已接受旅行邀请。" if accepted else "已拒绝旅行邀请。")


@router.post("/trip-invitations/{member_id}/accept", response_model=TripMembershipNotice)
async def accept_trip_invitation(
    member_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    return await _respond_to_invitation(
        member_id=member_id, user=user, session=session, accepted=True,
    )


@router.post("/trip-invitations/{member_id}/decline", response_model=TripMembershipNotice)
async def decline_trip_invitation(
    member_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    return await _respond_to_invitation(
        member_id=member_id, user=user, session=session, accepted=False,
    )
