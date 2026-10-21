from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.advisor.draft_service import (
    meal_schedule_advisories,
    meal_time_advisory,
    overlaps_time_window,
    validate_draft_selection,
)
from travelpilot_api.schemas.advisor import AdvisorDraftCreate


def _stop(candidate_id, day="2026-11-02", start="09:00"):
    return SimpleNamespace(
        candidate_id=candidate_id, day=date.fromisoformat(day),
        start_time=time.fromisoformat(start),
    )


def test_draft_selection_requires_all_locked_stops_at_their_original_time():
    locked_id = str(uuid4())
    candidate = {
        "id": locked_id,
        "name": "预约展览",
        "fixed_schedule": {"day": "2026-11-02", "start_time": "10:30"},
    }

    with pytest.raises(HTTPException, match="不能移除固定预约地点"):
        validate_draft_selection(
            [candidate], [], [], date(2026, 11, 2), date(2026, 11, 3),
        )
    with pytest.raises(HTTPException, match="不会擅自更改"):
        validate_draft_selection(
            [candidate], [locked_id], [_stop(locked_id, start="11:00")],
            date(2026, 11, 2), date(2026, 11, 3),
        )


def test_draft_selection_rejects_duplicate_or_out_of_trip_schedules():
    candidate_id = str(uuid4())
    candidate = {"id": candidate_id, "name": "景点"}
    with pytest.raises(HTTPException, match="无效或重复"):
        validate_draft_selection(
            [candidate], [candidate_id, candidate_id], [_stop(candidate_id)],
            date(2026, 11, 2), date(2026, 11, 3),
        )
    with pytest.raises(HTTPException, match="旅行日期范围"):
        validate_draft_selection(
            [candidate], [candidate_id], [_stop(candidate_id, day="2026-11-04")],
            date(2026, 11, 2), date(2026, 11, 3),
        )


def test_draft_selection_returns_exact_selected_rows_and_schedule_mapping():
    candidate_id = str(uuid4())
    candidate = {"id": candidate_id, "name": "景点"}
    schedule = _stop(candidate_id)

    selected, by_id = validate_draft_selection(
        [candidate], [candidate_id], [schedule], date(2026, 11, 2), date(2026, 11, 3),
    )

    assert selected == [candidate]
    assert by_id == {candidate_id: schedule}


def test_lodging_candidate_does_not_need_a_daily_schedule():
    lodging_id = str(uuid4())
    lodging = {"id": lodging_id, "name": "旅行酒店", "planning_category": "LODGING"}

    selected, by_id = validate_draft_selection(
        [lodging], [lodging_id], [], date(2026, 11, 2), date(2026, 11, 3),
    )

    assert selected == [lodging]
    assert by_id == {}


def test_lodging_and_scheduled_place_can_be_selected_together():
    lodging_id, attraction_id = str(uuid4()), str(uuid4())
    lodging = {"id": lodging_id, "name": "旅行酒店", "planning_category": "LODGING"}
    attraction = {"id": attraction_id, "name": "景点", "planning_category": "ATTRACTION"}
    schedule = _stop(attraction_id)

    selected, by_id = validate_draft_selection(
        [lodging, attraction], [lodging_id, attraction_id], [schedule],
        date(2026, 11, 2), date(2026, 11, 3),
    )

    assert selected == [lodging, attraction]
    assert by_id == {attraction_id: schedule}


@pytest.mark.parametrize(
    ("meal_type", "start", "expected"),
    [
        ("LUNCH", "12:30", None),
        ("LUNCH", "22:00", "午餐"),
        ("DINNER", "12:30", "晚餐"),
        ("BREAKFAST", "08:00", None),
        ("SNACK", "23:00", None),
    ],
)
def test_meal_time_advisory_flags_unusual_pairings_without_rejecting_them(meal_type, start, expected):
    candidate = {"name": "街角餐馆", "planning_category": "FOOD", "meal_type": meal_type}

    advisory = meal_time_advisory(candidate, time.fromisoformat(start))

    if expected is None:
        assert advisory is None
    else:
        assert expected in advisory
        assert start in advisory
        assert "请确认" in advisory


def test_meal_time_advisory_does_not_classify_an_attraction_as_a_meal():
    assert meal_time_advisory(
        {"name": "博物馆", "planning_category": "ATTRACTION", "meal_type": "LUNCH"},
        time(22, 0),
    ) is None


@pytest.mark.parametrize(
    ("start", "end", "window_start", "window_end", "expected"),
    [
        (10 * 60 + 30, 12 * 60, 11 * 60, 14 * 60, True),
        (16 * 60 + 30, 18 * 60, 17 * 60, 20 * 60, True),
        (13 * 60 + 40, 13 * 60 + 55, 11 * 60, 14 * 60, False),
        (14 * 60, 15 * 60, 11 * 60, 14 * 60, False),
        (12 * 60, 12 * 60, 11 * 60, 14 * 60, True),
    ],
)
def test_meal_window_coverage_uses_consistent_duration_overlap(
    start, end, window_start, window_end, expected,
):
    assert overlaps_time_window(start, end, window_start, window_end) is expected


def test_meal_schedule_advisories_only_report_selected_mis_timed_typed_meals():
    selected = [
        {"id": "late-lunch", "name": "午餐店", "planning_category": "FOOD", "meal_type": "LUNCH"},
        {"id": "on-time-dinner", "name": "晚餐店", "planning_category": "FOOD", "meal_type": "DINNER"},
        {"id": "museum", "name": "博物馆", "planning_category": "ATTRACTION", "meal_type": "LUNCH"},
    ]
    schedules = {
        "late-lunch": _stop("late-lunch", start="22:00"),
        "on-time-dinner": _stop("on-time-dinner", start="18:30"),
        "museum": _stop("museum", start="22:00"),
    }

    advisories = meal_schedule_advisories(selected, schedules)

    assert len(advisories) == 1
    assert "午餐店" in advisories[0]
    assert "22:00" in advisories[0]


def test_profile_memory_override_requires_explicit_draft_request_flag():
    draft = AdvisorDraftCreate(candidate_ids=[uuid4()])
    assert draft.allow_profile_memory_conflicts is False
    confirmed = AdvisorDraftCreate(
        candidate_ids=[uuid4()], allow_profile_memory_conflicts=True,
    )
    assert confirmed.allow_profile_memory_conflicts is True
