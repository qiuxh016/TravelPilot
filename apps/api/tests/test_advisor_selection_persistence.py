from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from travelpilot_api.advisor.context import _remap_selected_candidate_ids
from travelpilot_api.api.v1 import advisor
from travelpilot_api.schemas.advisor import AdvisorSelectionUpdate, AdvisorSessionContextUpdate


class SelectionDatabase:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True

    async def refresh(self, _row):
        return None

    async def scalar(self, _statement):
        return None


@pytest.mark.asyncio
async def test_selection_update_persists_selection_and_partial_schedule(monkeypatch):
    owner_id, session_id, meal_id, lodging_id = (uuid4() for _ in range(4))
    row = SimpleNamespace(
        id=session_id,
        owner_id=owner_id,
        status="ACTIVE",
        candidates=[
            {"id": str(meal_id), "planning_category": "FOOD"},
            {"id": str(lodging_id), "planning_category": "LODGING"},
        ],
        context={"revision": 4, "selected_candidate_ids": []},
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
        updated_at=None,
    )
    db = SelectionDatabase()

    async def owned_session(_db, requested_session_id, requested_owner_id, *, lock=False):
        assert requested_session_id == session_id
        assert requested_owner_id == owner_id
        assert lock is True
        return row

    async def read_session(_db, requested_row):
        assert requested_row is row
        return row

    monkeypatch.setattr(advisor, "owned_session", owned_session)
    monkeypatch.setattr(advisor, "read_session", read_session)
    payload = AdvisorSelectionUpdate(
        selected_candidate_ids=[meal_id, lodging_id],
        schedules=[{"candidate_id": meal_id, "day": "2026-11-02"}],
    )

    result = await advisor.update_session_selection(
        session_id, payload, db=db, user=SimpleNamespace(id=owner_id),
    )

    assert result is row
    assert row.context["selected_candidate_ids"] == [str(meal_id), str(lodging_id)]
    assert row.context["candidate_schedules"] == {
        str(meal_id): {"day": "2026-11-02"},
    }
    assert row.context["revision"] == 5
    assert db.committed is True
    assert db.added[0].metadata_json == {"selected_count": 2, "scheduled_count": 1}


@pytest.mark.asyncio
async def test_selection_update_rejects_unknown_ids_and_out_of_trip_dates(monkeypatch):
    owner_id, session_id, meal_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=session_id,
        owner_id=owner_id,
        status="ACTIVE",
        candidates=[{"id": str(meal_id), "planning_category": "FOOD"}],
        context={"revision": 2, "selected_candidate_ids": []},
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
        updated_at=None,
    )
    db = SelectionDatabase()

    async def owned_session(*_args, **_kwargs):
        return row

    monkeypatch.setattr(advisor, "owned_session", owned_session)
    async def read_session(*_args):
        return row

    monkeypatch.setattr(advisor, "read_session", read_session)

    with pytest.raises(HTTPException) as unknown:
        await advisor.update_session_selection(
            session_id,
            AdvisorSelectionUpdate(selected_candidate_ids=[uuid4()]),
            db=db,
            user=SimpleNamespace(id=owner_id),
        )
    assert unknown.value.status_code == 422

    with pytest.raises(HTTPException) as outside_range:
        await advisor.update_session_selection(
            session_id,
            AdvisorSelectionUpdate(
                selected_candidate_ids=[meal_id],
                schedules=[{"candidate_id": meal_id, "day": "2026-11-04"}],
            ),
            db=db,
            user=SimpleNamespace(id=owner_id),
        )
    assert outside_range.value.status_code == 422
    assert db.committed is False


@pytest.mark.asyncio
async def test_context_date_change_keeps_selection_and_time_but_clears_out_of_range_day(monkeypatch):
    owner_id, session_id, lunch_id, dinner_id = (uuid4() for _ in range(4))
    row = SimpleNamespace(
        id=session_id,
        owner_id=owner_id,
        status="ACTIVE",
        trip_id=None,
        destination="成都",
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
        budget=None,
        pace_level=3,
        preference_text="",
        candidates=[
            {"id": str(lunch_id), "planning_category": "FOOD"},
            {"id": str(dinner_id), "planning_category": "FOOD"},
        ],
        context={
            "revision": 4,
            "selected_candidate_ids": [str(lunch_id), str(dinner_id)],
            "candidate_schedules": {
                str(lunch_id): {"day": "2026-11-02", "start_time": "12:00"},
                str(dinner_id): {"day": "2026-11-03", "start_time": "18:00"},
            },
        },
        updated_at=None,
    )
    db = SelectionDatabase()
    monkeypatch.setattr(advisor, "owned_session", lambda *_args, **_kwargs: _async_return(row))
    monkeypatch.setattr(advisor, "planning_state", lambda _row: {"missing_required": []})
    monkeypatch.setattr(advisor, "trip_action_items", lambda *_args: [], raising=False)
    monkeypatch.setattr(advisor, "read_session", lambda *_args: _async_return(row))

    result = await advisor.update_session_context(
        session_id,
        AdvisorSessionContextUpdate(
            destination="成都",
            start_date="2026-11-03",
            end_date="2026-11-04",
        ),
        db=db,
        user=SimpleNamespace(id=owner_id),
    )

    assert result is row
    assert row.context["selected_candidate_ids"] == [str(lunch_id), str(dinner_id)]
    assert row.context["candidate_schedules"] == {
        str(lunch_id): {"start_time": "12:00"},
        str(dinner_id): {"day": "2026-11-03", "start_time": "18:00"},
    }
    assert row.context["revision"] == 5
    assert db.committed is True


async def _async_return(value):
    return value


def test_selection_update_requires_unique_selected_and_schedule_ids():
    candidate_id = uuid4()
    with pytest.raises(ValidationError):
        AdvisorSelectionUpdate(selected_candidate_ids=[candidate_id, candidate_id])
    with pytest.raises(ValidationError):
        AdvisorSelectionUpdate(
            selected_candidate_ids=[candidate_id],
            schedules=[
                {"candidate_id": candidate_id, "day": "2026-11-02"},
                {"candidate_id": candidate_id, "start_time": "12:00"},
            ],
        )


def test_context_refresh_rekeys_selected_candidate_and_its_schedule_by_poi_identity():
    context = {
        "revision": 7,
        "selected_candidate_ids": ["old-lunch-id"],
        "candidate_schedules": {
            "old-lunch-id": {"day": "2026-11-02", "start_time": "12:00"},
        },
    }
    previous = [{
        "id": "old-lunch-id", "existing_poi_id": "poi-lunch",
        "planning_category": "FOOD", "meal_type": "LUNCH",
    }]
    refreshed = [{
        "id": "new-lunch-id", "existing_poi_id": "poi-lunch",
        "planning_category": "FOOD", "meal_type": "LUNCH",
    }]

    _remap_selected_candidate_ids(context, previous, refreshed)

    assert context["selected_candidate_ids"] == ["new-lunch-id"]
    assert context["candidate_schedules"] == {
        "new-lunch-id": {"day": "2026-11-02", "start_time": "12:00"},
    }
    assert context["revision"] == 8
