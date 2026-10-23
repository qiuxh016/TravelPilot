from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from travelpilot_api.api.v1 import advisor_drafts
from travelpilot_api.schemas.advisor import AdvisorNegotiationRequest
from travelpilot_core.engines.constraints.negotiation import (
    NegotiationActivity,
    negotiate_schedule,
)

DAY_1 = date(2026, 11, 3)
DAY_2 = date(2026, 11, 4)


def activity(
    item_id: str,
    day: date,
    start: int,
    duration: int,
    **overrides,
) -> NegotiationActivity:
    return NegotiationActivity(
        id=item_id,
        name=item_id,
        day=day,
        start_minute=start,
        duration_minute=duration,
        **overrides,
    )


def test_conflict_generates_valid_low_cost_move_and_preserves_fixed_event():
    fixed = activity("show", DAY_1, 19 * 60, 90, fixed=True, required=True)
    movable = activity("museum", DAY_1, 18 * 60, 120, optional=True, priority=2)

    result = negotiate_schedule(
        [fixed, movable],
        start_date=DAY_1,
        end_date=DAY_2,
        travel_minutes={("show", "museum"): 0, ("museum", "show"): 0},
    )

    assert result.feasible
    assert result.conflicts[0].code == "OVERLAP"
    assert result.minimal_conflict_set == ("museum", "show")
    assert result.alternatives
    assert result.explored_states <= 4096
    for alternative in result.alternatives:
        by_id = {item.id: item for item in alternative.activities}
        assert by_id["show"] == fixed
        assert by_id["museum"] != movable
        assert alternative.changes[0]["operation"] == "MOVE"


def test_lunch_conflict_moves_meal_inside_user_visible_meal_window():
    lunch = activity(
        "lunch", DAY_1, 15 * 60, 60, category="LUNCH", opens_at=10 * 60, closes_at=20 * 60
    )

    result = negotiate_schedule(
        [lunch],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={},
    )

    assert result.feasible
    assert result.conflicts[0].code == "LUNCH_WINDOW"
    assert all(11 * 60 + 30 <= option.activities[0].start_minute for option in result.alternatives)
    assert all(option.activities[0].end_minute <= 14 * 60 for option in result.alternatives)


@pytest.mark.parametrize("start", [8 * 60, 22 * 60])
def test_valid_early_and_late_visits_are_not_restricted_to_default_tourist_hours(start):
    visit = activity("visit", DAY_1, start, 30)

    result = negotiate_schedule(
        [visit], start_date=DAY_1, end_date=DAY_1, travel_minutes={},
    )

    assert result.feasible
    assert result.conflicts == ()


def test_dinner_window_matches_existing_advisor_reference_window():
    dinner = activity("dinner", DAY_1, 20 * 60 + 15, 30, category="DINNER")

    result = negotiate_schedule(
        [dinner], start_date=DAY_1, end_date=DAY_1, travel_minutes={},
    )

    assert result.feasible
    assert result.conflicts[0].code == "DINNER_WINDOW"
    assert all(option.activities[0].end_minute <= 20 * 60 for option in result.alternatives)


def test_unknown_route_is_never_assumed_to_be_zero_or_surfaced_as_feasible():
    first = activity("first", DAY_1, 9 * 60, 60)
    second = activity("second", DAY_1, 12 * 60, 60)

    result = negotiate_schedule(
        [first, second],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={},
    )

    assert not result.feasible
    assert result.conflicts[0].code == "ROUTE_UNKNOWN"
    assert result.alternatives == ()


def test_two_conflicting_fixed_events_are_rejected_without_relaxing_hard_constraints():
    first = activity("appointment-a", DAY_1, 10 * 60, 90, fixed=True, required=True)
    second = activity("appointment-b", DAY_1, 10 * 60 + 30, 60, fixed=True, required=True)

    result = negotiate_schedule(
        [first, second],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={("appointment-a", "appointment-b"): 0},
    )

    assert not result.feasible
    assert result.conflicts[0].code == "OVERLAP"
    assert result.minimal_conflict_set == ("appointment-a", "appointment-b")
    assert result.alternatives == ()


def test_two_independent_conflicts_can_be_resolved_with_two_moves():
    items = [
        activity("a", DAY_1, 9 * 60, 60),
        activity("b", DAY_1, 9 * 60 + 30, 60),
        activity("c", DAY_1, 14 * 60, 60),
        activity("d", DAY_1, 14 * 60 + 30, 60),
    ]
    routes = {(left.id, right.id): 0 for left in items for right in items if left.id != right.id}

    result = negotiate_schedule(items, start_date=DAY_1, end_date=DAY_1, travel_minutes=routes)

    combined = [option for option in result.alternatives if len(option.changes) == 2]
    assert result.feasible
    assert combined
    assert all(
        len(option.changes) == 2
        for option in result.alternatives
        if option.title == "同时调整两项安排"
    )


def test_distinct_combined_schedule_options_have_distinct_ids():
    items = [
        activity("p0", DAY_1, 690, 90, priority=3),
        activity("p1", DAY_1, 870, 45, priority=2),
        activity("p2", DAY_1, 900, 90, priority=4),
        activity("p3", DAY_1, 990, 60, priority=3),
    ]
    routes = {
        (left.id, right.id): 0
        for left in items
        for right in items
        if left.id != right.id
    }

    result = negotiate_schedule(
        items,
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes=routes,
    )

    combined = [option for option in result.alternatives if len(option.changes) == 2]
    assert len(combined) >= 2
    assert len({option.id for option in combined}) == len(combined)


def test_activity_outside_hard_trip_date_is_rejected():
    outside = activity("outside", DAY_2, 10 * 60, 30)

    with pytest.raises(ValueError, match="outside the trip dates"):
        negotiate_schedule(
            [outside], start_date=DAY_1, end_date=DAY_1, travel_minutes={}
        )


def test_optional_removal_is_offered_only_for_optional_nonrequired_activity():
    fixed = activity("fixed", DAY_1, 10 * 60, 120, fixed=True, required=True)
    optional = activity(
        "optional",
        DAY_1,
        11 * 60,
        90,
        optional=True,
        priority=1,
        opens_at=9 * 60,
        closes_at=13 * 60,
    )

    result = negotiate_schedule(
        [fixed, optional],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={("fixed", "optional"): 0, ("optional", "fixed"): 0},
    )

    removals = [
        option for option in result.alternatives if option.changes[0]["operation"] == "REMOVE"
    ]
    assert removals
    assert [item.id for item in removals[0].activities] == ["fixed"]


def test_required_nonfixed_activity_is_not_moved_to_hide_an_opening_conflict():
    required = activity(
        "required-stop",
        DAY_1,
        9 * 60,
        90,
        required=True,
        opens_at=11 * 60,
        closes_at=17 * 60,
    )

    result = negotiate_schedule(
        [required],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={},
    )

    assert not result.feasible
    assert result.conflicts[0].code == "NOT_OPEN"
    assert result.alternatives == ()


def test_route_boundary_candidate_finds_only_feasible_minute_between_grid_slots():
    previous = activity("previous", DAY_1, 9 * 60, 30, fixed=True, required=True)
    visit = activity("visit", DAY_1, 10 * 60, 30)
    following = activity("following", DAY_1, 11 * 60, 30, fixed=True, required=True)

    result = negotiate_schedule(
        [previous, visit, following],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={("previous", "visit"): 20, ("visit", "following"): 0},
    )

    moved = [
        option for option in result.alternatives
        if any(change["operation"] == "MOVE" for change in option.changes)
    ]
    assert result.feasible
    assert moved
    assert any(
        change["activity_id"] == "visit" and change["after_start"] == "10:10"
        for option in moved
        for change in option.changes
    )


def test_closed_at_start_conflict_gets_only_opening_hour_valid_alternative():
    museum = activity(
        "museum",
        DAY_1,
        9 * 60,
        90,
        opens_at=11 * 60,
        closes_at=17 * 60,
    )

    result = negotiate_schedule(
        [museum],
        start_date=DAY_1,
        end_date=DAY_1,
        travel_minutes={},
    )

    assert result.feasible
    assert result.conflicts[0].code == "NOT_OPEN"
    assert all(option.activities[0].start_minute >= 11 * 60 for option in result.alternatives)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_alternatives": 4},
        {"max_states": 0},
    ],
)
def test_search_limits_are_validated(kwargs):
    with pytest.raises(ValueError):
        negotiate_schedule(
            [],
            start_date=DAY_1,
            end_date=DAY_1,
            travel_minutes={},
            **kwargs,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_kind", ["PAYLOAD_VERSION", "CONTEXT_REVISION"])
async def test_negotiate_endpoint_rejects_stale_draft(monkeypatch, stale_kind):
    row = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        draft_version=2,
        draft={"version": 2, "context_revision": 4, "stops": []},
        context={"revision": 5 if stale_kind == "CONTEXT_REVISION" else 4},
        start_date=DAY_1,
        end_date=DAY_1,
    )

    async def owned_session(*args, **kwargs):
        return row

    monkeypatch.setattr(advisor_drafts, "owned_session", owned_session)
    with pytest.raises(HTTPException) as error:
        await advisor_drafts.negotiate_draft(
            row.id,
            AdvisorNegotiationRequest(draft_version=1 if stale_kind == "PAYLOAD_VERSION" else 2),
            object(),
            SimpleNamespace(id=uuid4()),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_negotiate_endpoint_returns_only_revalidated_schedule_options(monkeypatch):
    first_id, second_id = str(uuid4()), str(uuid4())
    row = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        draft_version=3,
        draft={
            "version": 3,
            "context_revision": 5,
            "stops": [
                {
                    "candidate_id": first_id,
                    "name": "博物馆",
                    "day": DAY_1.isoformat(),
                    "start_time": "10:00:00",
                    "end_time": "12:00:00",
                    "planning_category": "ATTRACTION",
                },
                {
                    "candidate_id": second_id,
                    "name": "古街",
                    "day": DAY_1.isoformat(),
                    "start_time": "11:00:00",
                    "end_time": "12:00:00",
                    "planning_category": "ATTRACTION",
                },
            ],
        },
        context={
            "revision": 5,
            "agent_route_evidence": {
                "candidate_ids": [first_id, second_id],
                "context_revision": 5,
                "durations_min": [[0, 10], [10, 0]],
                "status": "SIMULATED",
                "source": "Local simulated maps",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "mode": "DRIVING",
            },
        },
        candidates=[
            {"id": first_id, "name": "博物馆", "duration_min": 120},
            {"id": second_id, "name": "古街", "duration_min": 60},
        ],
        start_date=DAY_1,
        end_date=DAY_1,
    )

    async def owned_session(*args, **kwargs):
        return row

    monkeypatch.setattr(advisor_drafts, "owned_session", owned_session)
    result = await advisor_drafts.negotiate_draft(
        row.id,
        AdvisorNegotiationRequest(draft_version=3),
        object(),
        SimpleNamespace(id=uuid4()),
    )

    assert result.status == "OPTIONS"
    assert result.conflicts
    assert result.notes == ["2 个地点缺少可核验的营业时间，本次未将其作为已检查条件。"]
    assert result.alternatives
    assert all(
        change.operation == "MOVE" for option in result.alternatives for change in option.changes
    )
    assert all(
        change.day is not None and change.start_time is not None
        for option in result.alternatives
        for change in option.changes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_kind", ["MISSING", "FAILED", "EXPIRED"])
async def test_negotiate_endpoint_fails_closed_without_fresh_route_evidence(monkeypatch, cache_kind):
    first_id, second_id = str(uuid4()), str(uuid4())
    cache = None
    if cache_kind == "FAILED":
        cache = {
            "candidate_ids": [first_id, second_id], "context_revision": 2,
            "durations_min": [[0, 15], [15, 0]], "status": "UNAVAILABLE",
            "source": None, "retrieved_at": datetime.now(UTC).isoformat(), "mode": "DRIVING",
        }
    elif cache_kind == "EXPIRED":
        cache = {
            "candidate_ids": [first_id, second_id], "context_revision": 2,
            "durations_min": [[0, 15], [15, 0]], "status": "SIMULATED",
            "source": "Local simulated maps",
            "retrieved_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
            "mode": "DRIVING",
        }
    context = {"revision": 2}
    if cache is not None:
        context["agent_route_evidence"] = cache
    row = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        draft_version=1,
        draft={
            "version": 1,
            "context_revision": 2,
            "stops": [
                {
                    "candidate_id": first_id,
                    "name": "A",
                    "day": DAY_1.isoformat(),
                    "start_time": "09:00:00",
                    "end_time": "10:00:00",
                },
                {
                    "candidate_id": second_id,
                    "name": "B",
                    "day": DAY_1.isoformat(),
                    "start_time": "12:00:00",
                    "end_time": "13:00:00",
                },
            ],
        },
        context=context,
        candidates=[{"id": first_id, "name": "A"}, {"id": second_id, "name": "B"}],
        start_date=DAY_1,
        end_date=DAY_1,
    )

    async def owned_session(*args, **kwargs):
        return row

    monkeypatch.setattr(advisor_drafts, "owned_session", owned_session)
    with pytest.raises(HTTPException) as error:
        await advisor_drafts.negotiate_draft(
            row.id,
            AdvisorNegotiationRequest(draft_version=1),
            object(),
            SimpleNamespace(id=uuid4()),
        )
    assert error.value.status_code == 409
    assert "路线证据" in error.value.detail
