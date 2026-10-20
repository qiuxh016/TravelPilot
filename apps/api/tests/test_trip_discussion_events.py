from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from travelpilot_api.api.v1 import trip_discussion


class RequestUntilMessage:
    def __init__(self):
        self.checks = 0

    async def is_disconnected(self):
        self.checks += 1
        return self.checks > 1


@pytest.mark.asyncio
async def test_discussion_stream_pushes_new_message(monkeypatch):
    trip_id = uuid4()
    user_id = uuid4()
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=user_id),
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None)),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [SimpleNamespace(
            id=uuid4(), trip_id=trip_id, author_user_id=user_id,
            author_display_name="旅行成员", body="午餐安排在博物馆附近吧。",
            created_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
        )])),
    )
    monkeypatch.setattr(trip_discussion, "get_trip_access", AsyncMock())
    monkeypatch.setattr(trip_discussion.asyncio, "sleep", AsyncMock())

    response = await trip_discussion.stream_trip_discussion_events(
        trip_id, RequestUntilMessage(), session, SimpleNamespace(id=user_id),
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert response.media_type == "text/event-stream"
    assert chunks[0].startswith("event: snapshot\ndata: ")
    assert chunks[1].startswith("event: discussion_message\ndata: ")
    assert "午餐安排在博物馆附近吧。" in chunks[1]
