from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.advisor import sessions as advisor_sessions
from travelpilot_api.advisor.sessions import owned_session, read_session
from travelpilot_infra.db.models.advisor import AdvisorSessionModel


class OwnerScopedSessionDouble:
    def __init__(self, session_id, owner_id):
        self.row = SimpleNamespace(id=session_id, owner_id=owner_id)

    async def scalar(self, statement):
        self.lock_mode = statement._for_update_arg
        params = statement.compile().params
        if params["id_1"] != self.row.id or params["owner_id_1"] != self.row.owner_id:
            return None
        return self.row


@pytest.mark.asyncio
async def test_advisor_session_is_visible_only_to_its_owner():
    session_id = uuid4()
    owner_id = uuid4()
    other_user_id = uuid4()
    db = OwnerScopedSessionDouble(session_id, owner_id)

    assert await owned_session(db, session_id, owner_id, lock=True) is db.row
    assert db.lock_mode.key_share is True

    with pytest.raises(HTTPException) as foreign_access:
        await owned_session(db, session_id, other_user_id)
    with pytest.raises(HTTPException) as missing_access:
        await owned_session(db, uuid4(), other_user_id)

    assert foreign_access.value.status_code == missing_access.value.status_code == 404
    assert foreign_access.value.detail == missing_access.value.detail == "顾问会话不存在"


@pytest.mark.asyncio
async def test_session_read_exposes_ephemeral_conflicts_from_all_active_profile_limits(monkeypatch):
    owner_id = uuid4()
    candidate = {"id": "museum-1", "name": "市立博物馆", "category": "MUSEUM"}
    created_at = datetime.now(UTC)
    row = AdvisorSessionModel(
        id=uuid4(), owner_id=owner_id, trip_id=None, confirmed_trip_id=None,
        confirmed_plan_id=None, mode="discover", destination="", start_date=None,
        end_date=None, budget=None, pace_level=3, preference_text="",
        candidates=[candidate], context={"revision": 0, "selected_candidate_ids": []},
        draft=None, draft_version=0, status="ACTIVE", created_at=created_at,
        updated_at=created_at,
    )
    memories = [
        {"id": f"hard-{index}", "constraint_type": "HARD_LIMIT", "text": "不去酒吧"}
        for index in range(5)
    ] + [{"id": "hard-6", "constraint_type": "HARD_LIMIT", "text": "不去博物馆"}]

    async def active_memories(_db, requested_owner):
        assert requested_owner == owner_id
        return memories

    class _Rows:
        def all(self):
            return []

    class _ReadSession:
        async def scalars(self, _statement):
            return _Rows()

    monkeypatch.setattr(advisor_sessions, "_active_personal_memories", active_memories)

    response = await read_session(_ReadSession(), row)

    assert response.candidates[0]["profile_memory_conflicts"]
    assert "不去博物馆" in response.candidates[0]["profile_memory_conflicts"][0]
    assert row.candidates == [candidate]
    assert "profile_memory_conflicts" not in row.context
