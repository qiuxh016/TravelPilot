from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import advisor
from travelpilot_api.schemas.advisor import AdvisorChecklistDecision


class DatabaseDouble:
    async def commit(self):
        pass

    async def refresh(self, _row):
        pass


@pytest.mark.asyncio
async def test_checklist_decision_is_saved_to_owned_session_context(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), context={"existing": "kept"}, draft={"version": 4},
        draft_version=4, updated_at=None,
    )

    async def owned_session(_db, _session_id, _owner_id, *, lock):
        assert lock is True
        return row

    async def read_session(_db, session):
        return session

    monkeypatch.setattr(advisor, "owned_session", owned_session)
    monkeypatch.setattr(advisor, "read_session", read_session)

    result = await advisor.update_checklist_decision(
        row.id,
        AdvisorChecklistDecision(
            item_key="MEAL_MISSING:2026-10-18:LUNCH", decision="NOT_NEEDED", draft_version=4
        ),
        db=DatabaseDouble(),
        user=SimpleNamespace(id=row.owner_id),
    )

    assert result is row
    assert row.context["existing"] == "kept"
    assert row.context["checklist_decisions"]["MEAL_MISSING:2026-10-18:LUNCH"] == {
        "decision": "NOT_NEEDED", "draft_version": 4
    }


@pytest.mark.asyncio
async def test_checklist_decision_rejects_stale_draft_version(monkeypatch):
    row = SimpleNamespace(id=uuid4(), owner_id=uuid4(), context={}, draft={"version": 5}, draft_version=5)

    async def owned_session(_db, _session_id, _owner_id, *, lock):
        return row

    monkeypatch.setattr(advisor, "owned_session", owned_session)

    with pytest.raises(HTTPException) as error:
        await advisor.update_checklist_decision(
            row.id,
            AdvisorChecklistDecision(
                item_key="MEAL_MISSING:2026-10-18:LUNCH", decision="LATER", draft_version=4
            ),
            db=DatabaseDouble(),
            user=SimpleNamespace(id=row.owner_id),
        )

    assert error.value.status_code == 409
