from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from travelpilot_api.api.v1 import advisor_messages
from travelpilot_api.schemas.advisor import AdvisorMessageFeedbackCreate


class FeedbackSession:
    def __init__(self, message):
        self.message = message
        self.statement = None
        self.committed = False

    async def scalar(self, statement):
        self.statement = statement
        return self.message if self.message and self.message.role == "assistant" else None

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_feedback_is_saved_on_the_owned_assistant_reply(monkeypatch):
    owner_id, session_id, message_id = uuid4(), uuid4(), uuid4()
    session_row = SimpleNamespace(id=session_id, owner_id=owner_id)
    message = SimpleNamespace(
        id=message_id,
        session_id=session_id,
        owner_id=owner_id,
        role="assistant",
        payload={"response_source": "MODEL"},
    )
    db = FeedbackSession(message)

    async def owned_session(_db, requested_session_id, requested_owner_id):
        assert requested_session_id == session_row.id
        assert requested_owner_id == owner_id
        return session_row

    monkeypatch.setattr(advisor_messages, "owned_session", owned_session)
    result = await advisor_messages.submit_message_feedback(
        session_id,
        message_id,
        AdvisorMessageFeedbackCreate(rating="NOT_HELPFUL", reason="MISSED_REQUEST"),
        db=db,
        user=SimpleNamespace(id=owner_id),
    )

    sql = str(db.statement)
    assert "advisor_messages.id" in sql
    assert "advisor_messages.session_id" in sql
    assert "advisor_messages.owner_id" in sql
    assert "advisor_messages.role" in sql
    assert message.payload["response_source"] == "MODEL"
    assert message.payload["advisor_feedback"]["rating"] == "NOT_HELPFUL"
    assert message.payload["advisor_feedback"]["reason"] == "MISSED_REQUEST"
    assert datetime.fromisoformat(message.payload["advisor_feedback"]["submitted_at"]).tzinfo
    assert result.message_id == message_id
    assert result.rating == "NOT_HELPFUL"
    assert db.committed is True


@pytest.mark.asyncio
async def test_feedback_does_not_accept_user_messages(monkeypatch):
    owner_id, session_id, message_id = uuid4(), uuid4(), uuid4()
    db = FeedbackSession(SimpleNamespace(
        id=message_id, session_id=session_id, owner_id=owner_id,
        role="user", payload=None,
    ))

    async def owned_session(_db, requested_session_id, requested_owner_id):
        assert requested_session_id == session_id
        assert requested_owner_id == owner_id

    monkeypatch.setattr(advisor_messages, "owned_session", owned_session)
    with pytest.raises(HTTPException) as missing:
        await advisor_messages.submit_message_feedback(
            session_id,
            message_id,
            AdvisorMessageFeedbackCreate(rating="HELPFUL"),
            db=db,
            user=SimpleNamespace(id=owner_id),
        )

    assert missing.value.status_code == 404
    assert db.committed is False


def test_feedback_reason_is_only_valid_for_negative_rating():
    with pytest.raises(ValidationError):
        AdvisorMessageFeedbackCreate(rating="HELPFUL", reason="TOO_VERBOSE")

    assert AdvisorMessageFeedbackCreate(rating="NOT_HELPFUL").reason is None
