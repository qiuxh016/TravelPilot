from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.trip_member import TripMemberModel

TripRole = Literal["OWNER", "EDITOR", "VIEWER"]
_ROLE_LEVEL = {"VIEWER": 1, "EDITOR": 2, "OWNER": 3}


@dataclass(frozen=True)
class TripAccess:
    trip: TripModel
    role: TripRole
    membership: TripMemberModel | None = None


def role_allows(actual_role: str, required_role: str) -> bool:
    """Return whether an active trip role grants the requested capability."""
    return _ROLE_LEVEL.get(actual_role, 0) >= _ROLE_LEVEL.get(required_role, 99)


async def get_trip_access(
    session: AsyncSession,
    *,
    trip_id: UUID,
    user_id: UUID,
    required_role: Literal["VIEWER", "EDITOR", "OWNER"] = "VIEWER",
    lock: bool = False,
) -> TripAccess:
    """Resolve current access on every request; friendship or an old grant is not access."""
    trip_statement = select(TripModel).where(TripModel.id == trip_id)
    if lock:
        trip_statement = trip_statement.with_for_update()
    trip = await session.scalar(trip_statement)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    if trip.owner_id == user_id:
        actual_role: TripRole = "OWNER"
        membership = None
    else:
        member_statement = select(TripMemberModel).where(
            TripMemberModel.trip_id == trip_id,
            TripMemberModel.user_id == user_id,
            TripMemberModel.status == "ACCEPTED",
        )
        if lock:
            member_statement = member_statement.with_for_update()
        membership = await session.scalar(member_statement)
        if membership is None:
            # Hide whether the trip exists from users who have no current membership.
            raise HTTPException(status_code=404, detail="Trip not found")
        actual_role = membership.role  # type: ignore[assignment]

    if not role_allows(actual_role, required_role):
        raise HTTPException(status_code=403, detail="Insufficient trip permissions")
    return TripAccess(trip=trip, role=actual_role, membership=membership)
