from dataclasses import replace
from datetime import date, time
from uuid import uuid4

import pytest
from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    search_weather_replacements,
    select_weather_replacement,
    select_weather_replacements,
)
from travelpilot_core.engines.impact_analysis.replan import Node


def node(name, start, end):
    return Node(
        uuid4(),
        name,
        "ATTRACTION",
        date(2026, 10, 1),
        time.fromisoformat(start),
        time.fromisoformat(end),
        3,
    )


def candidate(name, duration=45, opening=None, poi_id=None, priority=3):
    return ReplanCandidate(
        poi_id or uuid4(),
        name,
        "MUSEUM",
        30.6,
        104.0,
        duration,
        priority,
        0.5,
        opening or {"open": "09:00", "close": "20:00"},
        {"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"}, "source": "USER"},
    )


def test_replacement_preserves_neighbors_and_checks_directed_route_times():
    previous = node("previous", "12:00", "13:00")
    removed = node("removed", "14:00", "15:00")
    following = node("following", "16:00", "17:00")
    quick = candidate("quick")
    slow = candidate("slow")
    travel = {
        (previous.poi_id, quick.poi_id): 5,
        (quick.poi_id, following.poi_id): 5,
        (previous.poi_id, slow.poi_id): 25,
        (slow.poi_id, following.poi_id): 25,
    }

    replacement, reason = select_weather_replacement(
        removed,
        [previous, following],
        [slow, quick],
        travel,
        condition="RAIN",
    )

    assert reason == "OK"
    assert replacement is not None and replacement.poi_id == quick.poi_id
    assert replacement.start_time == time(14) and replacement.end_time == time(14, 45)
    assert following.start_time == time(16)


def test_replacement_rejects_closed_or_unknown_hours_and_missing_route_time():
    previous = node("previous", "12:00", "13:00")
    removed = node("removed", "14:00", "15:00")
    closed = candidate("closed", opening={"open": "09:00", "close": "14:30"})

    replacement, reason = select_weather_replacement(
        removed,
        [previous],
        [closed],
        {(previous.poi_id, closed.poi_id): 0},
        condition="RAIN",
    )
    assert replacement is None and reason == "CLOSED_AT_SLOT"

    unknown_hours = replace(candidate("unknown-hours"), opening_hours=None)
    replacement, reason = select_weather_replacement(
        removed,
        [],
        [unknown_hours],
        {},
        condition="RAIN",
    )
    assert replacement is None and reason == "OPENING_HOURS_UNKNOWN"

    indoor = candidate("missing-route")
    replacement, reason = select_weather_replacement(
        removed,
        [previous],
        [indoor],
        {},
        condition="RAIN",
    )
    assert replacement is None and reason == "ROUTE_TIME_UNKNOWN"


def test_replacement_reports_capacity_and_rejects_unverified_weather_profile():
    removed = node("removed", "14:00", "15:00")
    full_day = [
        node(f"n{index}", f"{9 + index:02}:00", f"{10 + index:02}:00") for index in range(6)
    ]
    alternative = candidate("alternative")

    replacement, reason = select_weather_replacement(
        removed,
        full_day,
        [alternative],
        {},
        condition="RAIN",
    )
    assert replacement is None and reason == "DAILY_CAPACITY"

    unknown = replace(alternative, environment_profile=None)
    replacement, reason = select_weather_replacement(
        removed,
        [],
        [unknown],
        {},
        condition="RAIN",
    )
    assert replacement is None and reason == "WEATHER_PROFILE"


def test_joint_search_avoids_greedy_candidate_assignment_trap():
    first_removed = node("first-removed", "12:00", "13:00")
    second_removed = node("second-removed", "15:00", "16:00")
    following = node("following", "18:00", "19:00")
    candidate_a = candidate("A")
    candidate_b = candidate("B")
    travel = {
        (candidate_a.poi_id, following.poi_id): 10,
        (candidate_b.poi_id, following.poi_id): 1,
        (candidate_a.poi_id, candidate_b.poi_id): 1,
        (candidate_b.poi_id, candidate_a.poi_id): 20,
    }

    greedy_first, reason = select_weather_replacement(
        first_removed,
        [following],
        [candidate_a, candidate_b],
        travel,
        condition="RAIN",
    )
    assignments = select_weather_replacements(
        [first_removed, second_removed],
        [following],
        [candidate_a, candidate_b],
        travel,
        condition="RAIN",
    )

    assert reason == "OK" and greedy_first is not None
    assert greedy_first.poi_id == candidate_b.poi_id
    assert [(replacement.name if replacement else None) for _, replacement, _ in assignments] == [
        "A",
        "B",
    ]
    assert all(reason == "OK" for _, _, reason in assignments)


def test_joint_objective_can_leave_a_low_value_long_detour_unfilled():
    removed = Node(
        uuid4(),
        "低优先级户外点",
        "ATTRACTION",
        date(2026, 10, 1),
        time(10),
        time(11),
        1,
        environment_profile={"exposure": "OUTDOOR", "sensitivity": {"RAIN": "HIGH"}},
    )
    following = node("following", "18:00", "19:00")
    far_candidate = candidate("far", priority=1)
    travel = {(far_candidate.poi_id, following.poi_id): 50}

    result = select_weather_replacements(
        [removed],
        [following],
        [far_candidate],
        travel,
        condition="RAIN",
    )

    assert result[0][1] is None
    assert result[0][2] == "OBJECTIVE_TRADEOFF"


def test_joint_search_rejects_invalid_objective_weights():
    removed = node("removed", "10:00", "11:00")
    with pytest.raises(ValueError, match="objective weight"):
        search_weather_replacements(
            [removed],
            [],
            [],
            {},
            condition="RAIN",
            objective_weights={"unknown": 1},
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        search_weather_replacements(
            [removed],
            [],
            [],
            {},
            condition="RAIN",
            objective_weights={"preference_penalty": -1},
        )
