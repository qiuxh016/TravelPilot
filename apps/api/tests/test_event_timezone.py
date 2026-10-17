from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from travelpilot_api.api.v1.events import _effective_local_datetime, create_event
from travelpilot_api.schemas.events import EventCreate


class FakeSession:
    def __init__(self):
        self.added = None

    def add(self, entity):
        self.added = entity

    async def commit(self):
        return None

    async def refresh(self, _entity):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_minutes", [480, 330, -300])
async def test_create_event_persists_the_submitted_utc_offset(offset_minutes):
    offset = timezone(timedelta(minutes=offset_minutes))
    local_time = datetime(2026, 10, 17, 0, 30, tzinfo=offset)
    trip = SimpleNamespace(
        id=uuid4(), start_date=date(2026, 10, 17), end_date=date(2026, 10, 18)
    )
    session = FakeSession()

    event = await create_event(
        trip.id,
        EventCreate(event_type="USER_ADD", effective_at=local_time, payload={"poi_id": str(uuid4())}),
        session=session,
        trip=trip,
    )

    assert event is session.added
    assert event.effective_timezone_offset_minutes == offset_minutes


@pytest.mark.parametrize(
    ("utc_time", "offset_minutes", "expected"),
    [
        (datetime(2026, 10, 16, 16, 30, tzinfo=UTC), 480,
         datetime(2026, 10, 17, 0, 30, tzinfo=timezone(timedelta(hours=8)))),
        (datetime(2026, 10, 17, 5, 30, tzinfo=UTC), 330,
         datetime(2026, 10, 17, 11, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))),
        (datetime(2026, 10, 17, 16, 0, tzinfo=UTC), -300,
         datetime(2026, 10, 17, 11, 0, tzinfo=timezone(timedelta(hours=-5)))),
    ],
)
def test_replan_restores_local_wall_clock_from_database_timestamp(
    utc_time, offset_minutes, expected
):
    event = SimpleNamespace(
        effective_at=utc_time,
        effective_timezone_offset_minutes=offset_minutes,
    )

    assert _effective_local_datetime(event) == expected
