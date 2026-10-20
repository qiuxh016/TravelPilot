from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip_collaboration import TripActivityModel


def record_trip_activity(
    session: AsyncSession,
    *,
    trip_id: UUID,
    event_type: str,
    summary: str,
    actor_user_id: UUID | None = None,
    actor_display_name: str = "旅行成员",
    payload: dict | None = None,
) -> TripActivityModel:
    event = TripActivityModel(
        trip_id=trip_id,
        actor_user_id=actor_user_id,
        actor_display_name=actor_display_name,
        event_type=event_type,
        summary=summary,
        payload=payload or {},
    )
    session.add(event)
    return event
