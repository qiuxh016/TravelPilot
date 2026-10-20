from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.advisor import confirm_service
from travelpilot_api.advisor.confirmation import (
    validate_confirmation_draft,
    validate_profile_memory_snapshot,
    validate_trip_snapshot,
)
from travelpilot_api.advisor.context import profile_memory_fingerprint


def test_confirmation_requires_exact_draft_version_and_context_revision():
    draft = {"stops": [{"candidate_id": "place-1"}], "context_revision": 4, "warnings": []}

    with pytest.raises(HTTPException, match="草案版本已变化"):
        validate_confirmation_draft(draft, 3, 2, 4)
    with pytest.raises(HTTPException, match="对话偏好或地点清单已变化"):
        validate_confirmation_draft(draft, 3, 3, 5)


def test_confirmation_rejects_changed_profile_memory_snapshot():
    saved_memories = [{
        "id": "m1", "category": "MUSEUM", "text": "不去博物馆",
        "constraint_type": "HARD_LIMIT",
    }]
    draft = {"profile_memory_fingerprint": profile_memory_fingerprint(saved_memories)}

    validate_profile_memory_snapshot(draft, profile_memory_fingerprint(saved_memories))
    with pytest.raises(HTTPException, match="账户旅行偏好已变化"):
        validate_profile_memory_snapshot(
            draft, profile_memory_fingerprint([{**saved_memories[0], "text": "更新限制"}]),
        )
    with pytest.raises(HTTPException, match="缺少账户偏好快照"):
        validate_profile_memory_snapshot({}, profile_memory_fingerprint([]))


@pytest.mark.asyncio
async def test_confirm_service_reloads_memory_before_any_plan_write(monkeypatch):
    saved = [{
        "id": "m1", "category": "MUSEUM", "text": "喜欢博物馆",
        "constraint_type": "PREFERENCE",
    }]
    current = [{**saved[0], "text": "不去博物馆", "constraint_type": "HARD_LIMIT"}]
    row = SimpleNamespace(
        status="ACTIVE", confirmed_trip_id=None, confirmed_plan_id=None,
        context={"revision": 2}, draft_version=1,
        draft={"profile_memory_fingerprint": profile_memory_fingerprint(saved)},
    )
    user = SimpleNamespace(id=uuid4())
    calls = []

    async def reload_memories(_db, owner_id):
        calls.append(owner_id)
        return current

    monkeypatch.setattr(confirm_service, "_active_personal_memories", reload_memories)
    with pytest.raises(HTTPException, match="账户旅行偏好已变化"):
        await confirm_service.confirm_advisor_draft(object(), row, SimpleNamespace(), user)

    assert calls == [user.id]


def test_confirmation_rejects_blocking_constraints_and_empty_drafts():
    with pytest.raises(HTTPException, match="没有可保存"):
        validate_confirmation_draft({"stops": [], "warnings": []}, 1, 1, 0)
    with pytest.raises(HTTPException, match="存在时间或交通冲突"):
        validate_confirmation_draft(
            {"stops": [{"candidate_id": "place-1"}], "warnings": ["阻断：时间重叠"]},
            1, 1, 0,
        )


def test_confirmation_allows_user_acknowledgement_of_nonblocking_meal_time_advisory():
    draft = {
        "context_revision": 2,
        "warnings": ["模拟川味餐馆标记为午餐，当前安排在 21:00，不在常见参考时段 11:00–14:00 内；请确认是否符合你的作息。"],
        "selected_candidate_ids": ["food-1"],
        "stops": [{
            "candidate_id": "food-1", "day": "2026-11-02",
            "start_time": "21:00", "end_time": "22:00", "planning_category": "FOOD",
        }],
    }

    validate_confirmation_draft(
        draft, draft_version=1, submitted_version=1, current_context_revision=2,
        trip_start_date=date(2026, 11, 2), trip_end_date=date(2026, 11, 2),
    )


def test_confirmation_rechecks_locked_stops_and_trip_date_bounds():
    current_plan = {"stops": [{
        "candidate_id": "locked-1", "name": "预约展览", "status": "RESERVED",
        "day": "2026-11-02", "start_time": "10:30",
    }]}
    base_draft = {
        "context_revision": 0, "warnings": [], "selected_candidate_ids": ["locked-1"],
        "stops": [{
            "candidate_id": "locked-1", "day": "2026-11-02", "start_time": "10:30",
            "end_time": "11:30", "planning_category": "ATTRACTION",
        }],
    }
    validate_confirmation_draft(
        base_draft, 1, 1, 0, trip_start_date=date(2026, 11, 2),
        trip_end_date=date(2026, 11, 3), current_plan=current_plan,
    )

    moved = {**base_draft, "stops": [{
        **base_draft["stops"][0], "start_time": "11:00",
    }]}
    with pytest.raises(HTTPException, match="不能移除或更改日期/开始时间"):
        validate_confirmation_draft(
            moved, 1, 1, 0, trip_start_date=date(2026, 11, 2),
            trip_end_date=date(2026, 11, 3), current_plan=current_plan,
        )

    outside_trip = {**base_draft, "stops": [{
        **base_draft["stops"][0], "day": "2026-11-04",
    }]}
    with pytest.raises(HTTPException, match="超出旅行日期"):
        validate_confirmation_draft(
            outside_trip, 1, 1, 0, trip_start_date=date(2026, 11, 2),
            trip_end_date=date(2026, 11, 3), current_plan=current_plan,
        )


def test_confirmation_rejects_malformed_or_duplicate_stop_times():
    draft = {
        "context_revision": 0, "warnings": [], "selected_candidate_ids": ["p1", "p2"],
        "stops": [
            {"candidate_id": "p1", "day": "2026-11-02", "start_time": "12:00",
             "end_time": "11:00", "planning_category": "ATTRACTION"},
            {"candidate_id": "p1", "day": "2026-11-02", "start_time": "13:00",
             "end_time": "14:00", "planning_category": "ATTRACTION"},
        ],
    }
    with pytest.raises(HTTPException, match="结束时间必须晚于开始时间"):
        validate_confirmation_draft(draft, 1, 1, 0)
    valid_stop = {**draft["stops"][1], "start_time": "13:00", "end_time": "14:00"}
    duplicate = {**draft, "stops": [valid_stop, valid_stop]}
    with pytest.raises(HTTPException, match="地点重复"):
        validate_confirmation_draft(duplicate, 1, 1, 0)


def test_confirmation_rejects_overlapping_stops_and_duplicate_selection_ids():
    first = {
        "candidate_id": "p1", "day": "2026-11-02", "start_time": "09:00",
        "end_time": "10:30", "planning_category": "ATTRACTION",
    }
    second = {
        "candidate_id": "p2", "day": "2026-11-02", "start_time": "10:00",
        "end_time": "11:00", "planning_category": "FOOD",
    }
    draft = {
        "context_revision": 0, "warnings": [], "selected_candidate_ids": ["p1", "p2"],
        "stops": [first, second],
    }
    with pytest.raises(HTTPException, match="时间重叠"):
        validate_confirmation_draft(draft, 1, 1, 0)

    duplicate_selection = {**draft, "selected_candidate_ids": ["p1", "p1", "p2"]}
    with pytest.raises(HTTPException, match="已选地点重复"):
        validate_confirmation_draft(duplicate_selection, 1, 1, 0)


def test_trip_snapshot_rejects_stale_plan_and_changed_trip_inputs():
    base = {"destination": "成都", "start_date": "2026-11-02", "end_date": "2026-11-03",
            "budget": None, "pace_level": 3}
    current = dict(base)
    with pytest.raises(HTTPException, match="原旅行计划已在其他位置更新"):
        validate_trip_snapshot(
            snapshot_plan={"id": "old", "version": 1},
            latest_plan_id="new", latest_plan_version=2,
            base_trip=base, current_trip=current,
        )
    changed = {**current, "budget": "5000"}
    with pytest.raises(HTTPException, match="旅行条件已在其他位置更新"):
        validate_trip_snapshot(
            snapshot_plan={"id": "plan-1", "version": 1},
            latest_plan_id="plan-1", latest_plan_version=1,
            base_trip=base, current_trip=changed,
        )
