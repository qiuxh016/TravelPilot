from dataclasses import replace

import pytest
from travelpilot_api.advisor.turn_plan import (
    AdvisorTurnPlan,
    build_advisor_turn_plan,
    ready_stage_names,
)


def _plan(**overrides):
    values = {
        "goal": "帮我安排旅行",
        "planning_category": None,
        "trip_available": True,
        "destination_available": True,
        "dates_available": True,
        "schedule_requested": False,
        "weather_requested": False,
        "preparation_requested": False,
        "discovery_requested": False,
        "search_requested": False,
        "route_requested": False,
        "available_search_tools": ["search_places", "get_trip_weather"],
        "known_facts": ["destination", "trip_dates"],
        "missing_facts": [],
    }
    return build_advisor_turn_plan(**(values | overrides))


def test_meal_plus_weather_has_meal_primary_and_only_approved_evidence_tools():
    plan = _plan(
        goal="帮我找午餐，顺便看看当天会不会下雨",
        planning_category="FOOD",
        weather_requested=True,
        search_requested=True,
    )

    search = plan.stage("SEARCH_WEATHER")
    assert plan.primary_task_type == "MEAL"
    assert search is not None and search.task_context.task_type == "MEAL"
    assert search.task_context.allowed_tools == ["get_trip_weather", "search_places"]
    assert plan.stage("SCHEDULE") is None
    assert plan.stage("ROUTE") is None


def test_meal_time_wording_does_not_start_a_sightseeing_schedule_workflow():
    plan = _plan(
        goal="请推荐午餐，看看午餐时间怎么安排",
        planning_category="FOOD",
        schedule_requested=True,
        search_requested=True,
    )

    assert plan.primary_task_type == "MEAL"
    assert plan.stage("SEARCH_WEATHER") is not None
    assert plan.stage("SCHEDULE") is None


def test_explicit_fatigue_replanning_builds_schedule_stage_but_plain_tiredness_does_not():
    from travelpilot_api.advisor.interpretation import _is_schedule_recommendation_request

    fatigue_request = "我今天有点累，帮我把后面的安排放松些"
    # The standalone classifier recognizes the intent. For a saved in-progress
    # plan, turn_service diverts this request to the event-replan preview flow.
    assert _is_schedule_recommendation_request(fatigue_request)

    plain_statement = "我今天很累"
    assert not _is_schedule_recommendation_request(plain_statement)
    no_change = _plan(goal=plain_statement, schedule_requested=False)
    assert no_change.stage("SCHEDULE") is None


def test_explicit_meal_plus_full_itinerary_request_runs_both_scoped_stages():
    from travelpilot_api.advisor.interpretation import _is_schedule_recommendation_request

    goal = "帮我规划整天行程，同时推荐午餐地点"
    plan = _plan(
        goal=goal,
        planning_category="FOOD",
        schedule_requested=_is_schedule_recommendation_request(goal),
        search_requested=True,
    )

    assert plan.primary_task_type == "MEAL"
    meal = plan.stage("SEARCH_WEATHER")
    assert meal is not None and meal.task_context.task_type == "MEAL"
    assert meal.task_context.allowed_tools == ["search_places"]
    assert plan.stage("SCHEDULE") is not None


def test_two_day_distribution_with_lunch_breaks_remains_a_schedule_request():
    from travelpilot_api.advisor.interpretation import (
        _is_schedule_recommendation_request,
        _planning_category_request,
    )

    goal = "三处都安排，按轻松节奏分两天排并留出午餐时间；先给待确认的时间建议"
    assert _planning_category_request(goal) == ("FOOD", "LUNCH")
    assert _is_schedule_recommendation_request(goal)
    plan = _plan(
        goal=goal,
        planning_category="FOOD",
        schedule_requested=_is_schedule_recommendation_request(goal),
        search_requested=False,
    )

    assert plan.stage("SCHEDULE") is not None


@pytest.mark.parametrize(
    "goal",
    [
        "帮我找午餐和晚餐的地方",
        "推荐早餐、午餐和晚餐",
    ],
)
def test_multiple_meal_types_do_not_force_all_candidates_into_first_meal(goal):
    from travelpilot_api.advisor.interpretation import _planning_category_request

    assert _planning_category_request(goal) == ("FOOD", None)


def test_explicit_attraction_schedule_plus_lunch_runs_both_scoped_stages():
    from travelpilot_api.advisor.interpretation import _is_schedule_recommendation_request

    goal = "安排景点并找午餐"
    assert _is_schedule_recommendation_request(goal)
    plan = _plan(
        goal=goal,
        planning_category="FOOD",
        schedule_requested=_is_schedule_recommendation_request(goal),
        search_requested=True,
    )

    assert plan.primary_task_type == "MEAL"
    search = plan.stage("SEARCH_WEATHER")
    assert search is not None and search.task_context.task_type == "MEAL"
    assert search.task_context.allowed_tools == ["search_places"]
    assert plan.stage("SCHEDULE") is not None


def test_negated_attraction_schedule_does_not_start_a_sightseeing_stage():
    from travelpilot_api.advisor.interpretation import _is_schedule_recommendation_request

    goal = "不要安排景点，只找午餐"
    assert not _is_schedule_recommendation_request(goal)
    plan = _plan(
        goal=goal,
        planning_category="FOOD",
        schedule_requested=True,
        search_requested=True,
    )

    assert plan.primary_task_type == "MEAL"
    assert plan.stage("SEARCH_WEATHER") is not None
    assert plan.stage("SCHEDULE") is None

    multi_day_goal = "不要安排两日游，只找午餐"
    assert not _is_schedule_recommendation_request(multi_day_goal)
    multi_day_plan = _plan(
        goal=multi_day_goal,
        planning_category="FOOD",
        schedule_requested=True,
        search_requested=True,
    )
    assert multi_day_plan.stage("SCHEDULE") is None

    casual_negation = "帮我找午餐并结合天气，不用安排景点行程"
    assert not _is_schedule_recommendation_request(casual_negation)
    from travelpilot_api.advisor.interpretation import (
        _explicit_compound_planning_categories,
        _planning_category_request,
    )

    assert _planning_category_request(casual_negation) == ("FOOD", "LUNCH")
    assert _explicit_compound_planning_categories(casual_negation) == ()
    casual_plan = _plan(
        goal=casual_negation,
        planning_category="FOOD",
        weather_requested=True,
        schedule_requested=_is_schedule_recommendation_request(casual_negation),
        search_requested=True,
    )
    assert casual_plan.stage("SEARCH_WEATHER") is not None
    assert casual_plan.stage("SCHEDULE") is None
    assert casual_plan.stage("ROUTE") is None


def test_explicit_lodging_plus_multiday_itinerary_request_runs_both_stages():
    from travelpilot_api.advisor.interpretation import _is_schedule_recommendation_request

    goal = "找一间酒店，再规划两日游行程"
    plan = _plan(
        goal=goal,
        planning_category="LODGING",
        schedule_requested=_is_schedule_recommendation_request(goal),
        search_requested=True,
    )

    assert plan.primary_task_type == "LODGING"
    lodging = plan.stage("SEARCH_WEATHER")
    assert lodging is not None and lodging.task_context.task_type == "LODGING"
    assert plan.stage("SCHEDULE") is not None


def test_rainy_day_discovery_uses_one_search_weather_stage():
    plan = _plan(
        goal="下雨天推荐室内景点",
        weather_requested=True,
        discovery_requested=True,
        search_requested=True,
    )

    search = plan.stage("SEARCH_WEATHER")
    assert plan.primary_task_type == "DISCOVERY"
    assert search is not None and search.task_context.task_type == "DISCOVERY"
    assert search.task_context.allowed_tools == ["get_trip_weather", "search_places"]
    assert len(plan.stages) == 1


def test_transport_and_weather_are_separate_scoped_stages():
    plan = _plan(
        goal="从人民公园到武侯祠怎么走，并查一下当天的天气",
        weather_requested=True,
        route_requested=True,
    )

    weather = plan.stage("SEARCH_WEATHER")
    route = plan.stage("ROUTE")
    assert plan.primary_task_type == "TRANSPORT"
    assert weather is not None and weather.task_context.task_type == "WEATHER"
    assert weather.task_context.allowed_tools == ["get_trip_weather"]
    assert route is not None and route.task_context.allowed_tools == ["get_trip_route"]
    assert plan.stage("SCHEDULE") is None


def test_free_exploration_route_request_is_a_waiting_stage_without_route_tool():
    plan = _plan(
        goal="人民公园到武侯祠怎么走",
        trip_available=False,
        destination_available=False,
        dates_available=False,
        route_requested=True,
        available_search_tools=[],
        known_facts=[],
        missing_facts=[],
    )

    route = plan.stage("ROUTE")
    assert plan.primary_task_type == "TRANSPORT"
    assert route is not None
    assert route.task_context.allowed_tools == []
    assert route.task_context.missing_facts == ["selected_trip"]


def test_schedule_and_weather_keep_a_confirmable_schedule_primary():
    plan = _plan(
        goal="帮我重新安排时间，也看看天气",
        schedule_requested=True,
        weather_requested=True,
    )

    search = plan.stage("SEARCH_WEATHER")
    schedule = plan.stage("SCHEDULE")
    assert plan.primary_task_type == "SCHEDULE"
    assert search is not None and search.task_context.task_type == "WEATHER"
    assert schedule is not None
    assert schedule.task_context.allowed_tools == ["build_schedule_draft", "get_distance_matrix"]
    assert schedule.task_context.approval_boundary == "DRAFT_ONLY"
    assert schedule.depends_on == ()
    assert ready_stage_names(plan, stage_statuses={}) == ["SEARCH_WEATHER", "SCHEDULE"]


def test_schedule_and_route_are_both_planned_as_separate_scoped_stages():
    plan = _plan(
        goal="帮我安排好几个地点的时间，并查询景点之间怎么走",
        schedule_requested=True,
        route_requested=True,
    )

    assert plan.primary_task_type == "SCHEDULE"
    assert plan.stage("SCHEDULE") is not None
    route = plan.stage("ROUTE")
    assert route is not None
    assert route.task_context.task_type == "TRANSPORT"
    assert route.task_context.allowed_tools == ["get_trip_route"]


def test_cross_domain_stages_wait_for_requested_search_evidence_before_route_or_schedule():
    plan = _plan(
        goal="帮我找几个景点，再规划时间并查询景点之间的路线",
        schedule_requested=True,
        route_requested=True,
        search_requested=True,
        discovery_requested=True,
    )

    search = plan.stage("SEARCH_WEATHER")
    schedule = plan.stage("SCHEDULE")
    route = plan.stage("ROUTE")
    assert search is not None and schedule is not None and route is not None
    assert schedule.depends_on == ("SEARCH_WEATHER",)
    assert route.depends_on == ("SEARCH_WEATHER",)
    assert [stage.name for stage in plan.ready_stages()] == ["SEARCH_WEATHER"]
    assert [stage.name for stage in plan.ready_stages({"SEARCH_WEATHER"})] == ["SCHEDULE", "ROUTE"]


@pytest.mark.parametrize(
    ("search_status", "expected_ready"),
    [
        ("SUCCEEDED", ["SCHEDULE", "ROUTE"]),
        ("NEEDS_USER_INPUT", []),
        ("FAILED", []),
        ("REJECTED", []),
    ],
)
def test_requested_search_gates_dependent_schedule_and_route(search_status, expected_ready):
    plan = _plan(
        goal="找几个景点，再规划时间并查询景点之间的路线",
        schedule_requested=True,
        route_requested=True,
        search_requested=True,
        discovery_requested=True,
    )

    assert ready_stage_names(
        plan,
        stage_statuses={"SEARCH_WEATHER": search_status},
        already_executed={"SEARCH_WEATHER"},
    ) == expected_ready


def test_turn_plan_rejects_duplicate_stage_names():
    plan = _plan(search_requested=True, discovery_requested=True)
    assert plan.stages

    with pytest.raises(ValueError, match="unique names"):
        AdvisorTurnPlan(
            primary_task_type=plan.primary_task_type,
            stages=(plan.stages[0], plan.stages[0]),
        )


@pytest.mark.parametrize(
    ("stage_updates", "message"),
    [
        ({"SEARCH_WEATHER": ("SCHEDULE",)}, "unknown dependencies"),
        ({"SEARCH_WEATHER": ("ROUTE",), "ROUTE": ("SEARCH_WEATHER",)}, "acyclic"),
        ({"SEARCH_WEATHER": ("SEARCH_WEATHER",)}, "cannot depend on itself"),
    ],
    ids=["unknown", "cycle", "self-dependency"],
)
def test_turn_plan_rejects_invalid_dependency_graph(stage_updates, message):
    plan = _plan(
        goal="搜索景点并查询路线",
        search_requested=True,
        discovery_requested=True,
        route_requested=True,
    )
    stages = tuple(
        replace(stage, depends_on=stage_updates[stage.name])
        if stage.name in stage_updates else stage
        for stage in plan.stages
    )

    with pytest.raises(ValueError, match=message):
        AdvisorTurnPlan(primary_task_type=plan.primary_task_type, stages=stages)


def test_weather_only_and_saved_place_route_are_independent_but_poi_search_is_not():
    plan = _plan(
        goal="查一下旅行天气并确认已保存地点之间的路线",
        schedule_requested=False,
        weather_requested=True,
        search_requested=False,
        route_requested=True,
    )

    weather = plan.stage("SEARCH_WEATHER")
    route = plan.stage("ROUTE")
    assert weather is not None and route is not None
    assert route.depends_on == ()
    assert [stage.name for stage in plan.ready_stages()] == ["SEARCH_WEATHER", "ROUTE"]

    search_plan = _plan(
        goal="搜索景点并确认路线",
        schedule_requested=False,
        weather_requested=False,
        search_requested=True,
        route_requested=True,
    )
    search_route = search_plan.stage("ROUTE")
    assert search_route is not None
    assert search_route.depends_on == ("SEARCH_WEATHER",)


def test_schedule_reply_reports_route_and_keeps_changes_confirmable():
    from travelpilot_api.advisor.turn_service import _schedule_proposal_reply

    reply = _schedule_proposal_reply(
        candidate_count=3, route_evidence="VERIFIED", transport_route_requested=True,
        route={
            "origin_name": "人民公园", "destination_name": "武侯祠", "distance_m": 1800,
            "duration_min": 24, "mode": "WALKING", "source": "AMap Web Service",
            "retrieved_at": "2026-10-18T12:00:00Z",
        },
        route_question=None,
        suggestions=[{
            "candidate_id": "park-1", "day": "2026-10-22", "start_time": "09:00",
        }],
        candidates=[{"id": "park-1", "name": "人民公园"}],
        start_date="2026-10-22",
    )

    assert "3 个地点" in reply
    assert "已按草案安排：第1天（10月22日）：人民公园 09:00" in reply
    assert "本轮纳入排程" in reply
    assert "当前计划中已确认" not in reply
    assert "已核验的地图交通时间" in reply
    assert "人民公园" in reply and "武侯祠" in reply
    assert "待确认草案" in reply
    assert "才会生成新计划" in reply


def test_schedule_reply_discloses_route_failure_or_asks_user():
    from travelpilot_api.advisor.turn_service import _schedule_proposal_reply

    failed = _schedule_proposal_reply(
        candidate_count=2, route_evidence=None, transport_route_requested=True,
        route=None, route_question=None,
    )
    needs_input = _schedule_proposal_reply(
        candidate_count=2, route_evidence="SIMULATED", transport_route_requested=True,
        route=None, route_question="请确认是否接受驾车路线。",
    )

    assert "路线未核验" in failed
    assert "本轮未能核验单独的路线查询结果" in failed
    assert "路线部分还需要你确认" in needs_input
    assert "请确认是否接受驾车路线" in needs_input


def test_schedule_reply_skips_unmatched_or_malformed_assignment_facts():
    from travelpilot_api.advisor.turn_service import _schedule_proposal_reply

    reply = _schedule_proposal_reply(
        candidate_count=2,
        route_evidence=None,
        transport_route_requested=False,
        route=None,
        route_question=None,
        suggestions=[
            {"candidate_id": "unknown", "day": "2026-10-22", "start_time": "09:00"},
            {"candidate_id": "bad-date", "day": "not-a-date", "start_time": "10:00"},
        ],
        candidates=[{"id": "bad-date", "name": "忽略项"}],
        start_date="2026-10-22",
    )

    assert "已按草案安排" not in reply
    assert "忽略项" not in reply
    assert "unknown" not in reply


@pytest.mark.parametrize(
    ("trip_available", "destination_available", "dates_available", "schedule_requested", "weather_requested", "expected"),
    [
        (False, False, False, True, False, "GENERAL"),
        (True, True, False, False, True, "WEATHER"),
    ],
)
def test_plan_does_not_create_unavailable_execution_stages(
    trip_available, destination_available, dates_available,
    schedule_requested, weather_requested, expected,
):
    plan = _plan(
        trip_available=trip_available,
        destination_available=destination_available,
        dates_available=dates_available,
        schedule_requested=schedule_requested,
        weather_requested=weather_requested,
    )

    assert plan.primary_task_type == expected
    assert plan.stages == ()
