from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import plans


class RequestUntilFirstSnapshot:
    def __init__(self):
        self.checks = 0

    async def is_disconnected(self):
        self.checks += 1
        return self.checks > 1


@pytest.mark.asyncio
async def test_trip_event_stream_sends_current_plan_snapshot(monkeypatch):
    trip_id = uuid4()
    owner_id = uuid4()
    plan_id = uuid4()
    user = SimpleNamespace(id=owner_id)
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=owner_id),
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: SimpleNamespace(
            id=plan_id,
            version=4,
            created_by_user_id=owner_id,
            created_by_display_name="旅行组织者",
            created_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
        ))),
    )
    monkeypatch.setattr(plans, "_access", AsyncMock())
    monkeypatch.setattr(plans.asyncio, "sleep", AsyncMock())

    response = await plans.stream_trip_events(trip_id, RequestUntilFirstSnapshot(), session, user)
    chunks = [chunk async for chunk in response.body_iterator]

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert len(chunks) == 1
    assert chunks[0].startswith("event: snapshot\ndata: ")
    assert '"plan_version": 4' in chunks[0]
    assert '"updated_by": "旅行组织者"' in chunks[0]
