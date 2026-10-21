from datetime import UTC, date, datetime
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
async def test_negotiate_endpoint_rejects_stale_draft(monkeypatch):
    row = SimpleNamespace(
        id=uuid4(),
        status="ACTIVE",
        draft_version=2,
        draft={"version": 2, "context_revision": 4, "stops": []},
        context={"revision": 4},
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
    assert "刷新" in error.value.detail


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
async def test_negotiate_endpoint_fails_closed_without_fresh_route_evidence(monkeypatch):
    first_id, second_id = str(uuid4()), str(uuid4())
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
        context={"revision": 2},
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
