from travelpilot_api.advisor.turn_observation import build_stage_selection_observation
from travelpilot_api.advisor.turn_plan import build_advisor_turn_plan


def _plan():
    return build_advisor_turn_plan(
        goal="找景点、排时间并查路线",
        planning_category=None,
        trip_available=True,
        destination_available=True,
        dates_available=True,
        schedule_requested=True,
        weather_requested=False,
        preparation_requested=False,
        discovery_requested=True,
        search_requested=True,
        route_requested=True,
        available_search_tools=["search_places"],
    )


def test_stage_observation_uses_server_owned_facts_and_exact_plan_contracts():
    plan = _plan()
    observation = build_stage_selection_observation(
        user_goal="找景点、排时间并查路线",
        destination_available=True,
        dates_available=True,
        saved_place_count=4,
        confirmed_stop_count=2,
        completed_stage_observations={
            "search_weather": {"candidate_count": 3, "search_failures": 0},
        },
        plan=plan,
    )

    assert observation["trip_facts"] == {
        "destination_available": True,
        "dates_available": True,
        "saved_places": 4,
        "confirmed_stops": 2,
    }
    assert observation["completed_stage_observations"] == {
        "search_weather": {"candidate_count": 3, "search_failures": 0},
    }
    assert observation["stage_contracts"]["SCHEDULE"]["depends_on"] == [
        "SEARCH_WEATHER",
    ]
    assert observation["stage_contracts"]["ROUTE"]["allowed_tools"] == [
        "get_trip_route",
    ]
    assert set(observation["stage_contracts"]) == {stage.name for stage in plan.stages}


def test_stage_observation_bounds_goal_and_nonnegative_counts():
    observation = build_stage_selection_observation(
        user_goal="x" * 700,
        destination_available=False,
        dates_available=False,
        saved_place_count=-5,
        confirmed_stop_count=2000,
        completed_stage_observations={},
        plan=_plan(),
    )

    assert len(observation["user_goal"]) == 500
    assert observation["trip_facts"]["saved_places"] == 0
    assert observation["trip_facts"]["confirmed_stops"] == 1000


def test_stage_observation_only_includes_allowlisted_scalar_summaries():
    observation = build_stage_selection_observation(
        user_goal="查询路线",
        destination_available=True,
        dates_available=True,
        saved_place_count=2,
        confirmed_stop_count=0,
        completed_stage_observations={
            "search_weather": {
                "candidate_count": 8_000,
                "weather_available": True,
                "raw_user_text": "private details",
                "search_failures": "unknown",
            },
            "unplanned": {"private": "value"},
        },
        plan=_plan(),
    )

    assert observation["completed_stage_observations"] == {
        "search_weather": {"candidate_count": 1000, "weather_available": True},
    }
