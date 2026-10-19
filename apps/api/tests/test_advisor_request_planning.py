from travelpilot_api.advisor.request_planning import (
    extract_explicit_destination_city,
    is_schedule_proposal_edit_followup,
    plan_advisor_request,
    should_start_destination_recommendation_from_chat,
)
from travelpilot_core.agents.extraction import Extraction, RequestedTask


def _extract(*, intent="recommend", places=None, search_queries=None, tasks=None, pace="none"):
    return Extraction(
        intent=intent, places=places or [], search_queries=search_queries or [],
        requested_tasks=tasks or [], pace_adjustment=pace,
    )


def _plan(goal, **overrides):
    values = {
        "goal": goal,
        "extraction": _extract(),
        "continuing_task": False,
        "previous_payload": None,
        "unsupported_booking": False,
        "transport_route_requested": False,
        "has_trip": True,
        "current_plan_stops": [],
        "saved_candidates": [],
    }
    values.update(overrides)
    return plan_advisor_request(**values)


def test_broad_discovery_combines_verified_interest_terms_and_safe_defaults():
    plan = _plan(
        "推荐一些地点",
        extraction=_extract(search_queries=["历史景点"]),
    )

    assert plan.allowed_search_queries == ("历史景点", "景点", "美食")
    assert [item["keyword"] for item in plan.search_requests] == ["历史景点", "景点", "美食"]
    assert plan.recommendation_requested is True


def test_open_ended_recommendation_defaults_remain_optional_for_clarification():
    plan = _plan("推荐一些地点")

    assert plan.search_requests
    assert all(item.get("required") is not True for item in plan.search_requests)


def test_model_recommend_intent_alone_does_not_expand_a_general_advice_request():
    plan = _plan(
        "结合我这段旅行的目的地和已收藏地点，给我一个实用建议。",
        extraction=_extract(intent="recommend"),
    )

    assert plan.recommendation_requested is False
    assert plan.search_requests == ()


def test_explicit_natural_place_recommendation_still_starts_discovery():
    plan = _plan("帮我推荐几个好玩的地方")

    assert plan.recommendation_requested is True
    assert plan.search_requests


def test_food_only_request_never_adds_attraction_default():
    plan = _plan("推荐午餐地点")

    assert plan.requested_category == "FOOD"
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务"]


def test_negated_meal_does_not_start_food_search_or_assign_meal_slot():
    plan = _plan("推荐几个景点，不要安排午餐")

    assert plan.requested_category is None
    assert plan.requested_meal_type is None
    assert "餐饮服务" not in [item["keyword"] for item in plan.search_requests]


def test_positive_meal_after_negated_meal_selects_only_positive_slot():
    plan = _plan("不要安排午餐，帮我找晚餐")

    assert plan.requested_category == "FOOD"
    assert plan.requested_meal_type == "DINNER"
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务"]


def test_compound_request_keeps_each_explicit_domain_scoped():
    plan = _plan("找些景点并推荐午餐", extraction=_extract(search_queries=["历史景点"]))

    assert plan.compound_search_categories == ("FOOD", "ATTRACTION")
    assert plan.requested_category is None
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务", "景点"]
    assert all(item["required"] is True for item in plan.search_requests)


def test_current_message_negation_filters_broad_defaults_after_memory_queries():
    plan = _plan(
        "推荐一些地方，不要美食",
        extraction=_extract(search_queries=["美食", "历史景点"]),
    )

    assert plan.allowed_search_queries == ("历史景点", "景点")
    assert "美食" not in [item["keyword"] for item in plan.search_requests]


def test_followup_restores_meal_scope_and_only_valid_meal_type():
    plan = _plan(
        "先推荐几个",
        continuing_task=True,
        previous_payload={
            "schedule_recommendation_requested": True,
            "planning_category": "FOOD",
            "meal_type": "DINNER",
        },
    )

    assert plan.requested_category == "FOOD"
    assert plan.requested_meal_type == "DINNER"
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务"]


def test_schedule_followup_does_not_search_for_meals_just_to_reserve_a_break():
    plan = _plan(
        "把收藏景点排成两日游；三处都安排，节奏轻松并留出午餐时间。",
        extraction=_extract(search_queries=["餐饮服务", "景点"]),
        continuing_task=True,
        continuing_task_type="SCHEDULE",
        current_message="三处都安排，按轻松节奏分两天排并留出午餐时间。",
        previous_payload={"schedule_recommendation_requested": True},
    )

    assert plan.requested_category is None
    assert plan.search_requests == ()
    assert plan.recommendation_requested is False


def test_schedule_clarification_resumes_an_explicit_food_and_schedule_request():
    plan = _plan(
        "帮我规划整天行程，同时推荐午餐地点",
        continuing_task=True,
        continuing_task_type="SCHEDULE",
        current_message="轻松一点",
        previous_payload={
            "planning_category": "FOOD",
            "meal_type": "LUNCH",
            "schedule_recommendation_requested": True,
        },
    )

    assert plan.requested_category == "FOOD"
    assert plan.requested_meal_type == "LUNCH"
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务"]


def test_initial_schedule_with_meal_break_is_not_misrouted_to_food_search():
    plan = _plan(
        "请把收藏的人民公园和武侯祠排成两日游，每天留一小时午餐。",
        extraction=_extract(
            intent="recommend",
            places=[
                {"name": "人民公园", "confidence": 0.9, "action": "add"},
                {"name": "武侯祠", "confidence": 0.9, "action": "add"},
            ],
            search_queries=["餐饮服务"],
            tasks=[RequestedTask(kind="FOOD_SEARCH", evidence="午餐")],
        ),
        saved_candidates=[{"name": "人民公园"}, {"name": "武侯祠"}],
        current_message="请把收藏的人民公园和武侯祠排成两日游，每天留一小时午餐。",
    )

    assert plan.requested_category is None
    assert plan.compound_search_categories == ()
    assert plan.search_requests == ()
    assert plan.recommendation_requested is False


def test_unconfirmed_schedule_proposal_edit_is_not_misread_as_food_search():
    previous = {
        "action": "SCHEDULE_PROPOSAL",
        "schedule_suggestions": [{"candidate_id": "saved-1", "day": "2026-10-22"}],
    }
    message = "请让第二天上午留空，午餐留出一小时。"

    assert is_schedule_proposal_edit_followup(previous, message)
    assert not is_schedule_proposal_edit_followup(
        previous, "另外推荐午餐餐厅。",
    )
    assert not is_schedule_proposal_edit_followup({}, message)

    failed_attempt = {
        "schedule_recommendation_requested": True,
        "agent_task": {"task_type": "SCHEDULE", "status": "FAILED"},
    }
    assert is_schedule_proposal_edit_followup(failed_attempt, message)

    plan = _plan(
        message,
        continuing_task=True,
        continuing_task_type="SCHEDULE",
        current_message=message,
        previous_payload=previous,
    )
    assert plan.requested_category is None
    assert plan.search_requests == ()


def test_schedule_followup_allows_an_explicit_request_to_search_for_lunch():
    plan = _plan(
        "安排已收藏景点并留出午餐时间。",
        continuing_task=True,
        continuing_task_type="SCHEDULE",
        current_message="顺便帮我推荐午餐餐厅。",
    )

    assert plan.requested_category == "FOOD"
    assert [item["keyword"] for item in plan.search_requests] == ["餐饮服务"]


def test_negated_place_search_does_not_resume_search_during_schedule_followup():
    plan = _plan(
        "安排收藏地点。",
        continuing_task=True,
        continuing_task_type="SCHEDULE",
        current_message="不用找餐厅，午餐只留出一小时。",
    )

    assert plan.search_requests == ()
    assert plan.requested_category is None


def test_route_and_unsupported_booking_do_not_start_map_search():
    route = _plan("从人民公园到武侯祠怎么走", transport_route_requested=True)
    booking = _plan("帮我预订酒店", unsupported_booking=True)

    assert route.search_requests == ()
    assert route.transport_route_requested is True
    assert booking.search_requests == ()
    assert booking.transport_route_requested is False
    assert booking.recommendation_requested is False


def test_event_handoff_requires_an_existing_trip_and_saved_stops():
    no_plan = _plan("下雨了，帮我调整", has_trip=True, current_plan_stops=[])
    with_plan = _plan(
        "下雨了，帮我调整",
        has_trip=True,
        current_plan_stops=[{"candidate_id": "stop-1"}],
    )

    assert no_plan.event_replan_handoff is False
    assert with_plan.event_replan_handoff is True
    assert with_plan.event_replan_type == "WEATHER_CHANGE"


def test_explicit_destination_city_is_extracted_from_travel_intent():
    assert extract_explicit_destination_city("我想去杭州周末玩，推荐一些景点") == "杭州"
    assert extract_explicit_destination_city("推荐泉州周边的历史街区") == "泉州"


def test_explicit_destination_city_ignores_origins_and_rejected_cities():
    assert extract_explicit_destination_city("我在杭州，推荐几个周末去处") is None
    assert extract_explicit_destination_city("推荐几个适合周末去的城市") is None
    assert extract_explicit_destination_city("不想去北京，推荐别的城市") is None
    assert extract_explicit_destination_city("不想去北京，想去厦门周末玩") == "厦门"


def test_open_destination_request_is_separate_from_city_scoped_discovery():
    assert should_start_destination_recommendation_from_chat(
        mode="discover", trip_id=None, destination=None,
        recommendation_requested=True, requested_category=None,
        compound_search_categories=(),
    )
    assert not should_start_destination_recommendation_from_chat(
        mode="discover", trip_id=None, destination="杭州",
        recommendation_requested=True, requested_category=None,
        compound_search_categories=(),
    )
    assert not should_start_destination_recommendation_from_chat(
        mode="discover", trip_id=None, destination=None,
        recommendation_requested=True, requested_category="FOOD",
        compound_search_categories=(),
    )
    assert not should_start_destination_recommendation_from_chat(
        mode="existing", trip_id=None, destination=None,
        recommendation_requested=True, requested_category=None,
        compound_search_categories=(),
    )


def test_model_task_router_recognizes_user_grounded_paraphrased_weather_route_and_meal_requests():
    weather_text = "Will I need a rain shell during this trip?"
    weather = _plan(
        weather_text,
        extraction=_extract(tasks=[
            RequestedTask(kind="WEATHER_CHECK", evidence="need a rain shell"),
            RequestedTask(kind="PREPARATION_ADVICE", evidence="need a rain shell"),
        ]),
    )
    route_text = "大熊猫基地和宽窄巷子之间坐地铁大概要多久？"
    route = _plan(
        route_text,
        extraction=_extract(tasks=[
            RequestedTask(kind="ROUTE_QUERY", evidence="之间坐地铁大概要多久"),
        ]),
    )
    meal_text = "中午有什么值得吃的？"
    meal = _plan(
        meal_text,
        extraction=_extract(tasks=[
            RequestedTask(kind="FOOD_SEARCH", evidence="中午有什么值得吃的"),
        ]),
    )

    assert weather.weather_requested is True
    assert weather.preparation_requested is True
    assert route.transport_route_requested is True
    assert route.search_requests == ()
    assert meal.requested_category == "FOOD"
    assert [item["keyword"] for item in meal.search_requests] == ["餐饮服务"]


def test_model_task_router_requires_current_positive_verbatim_evidence():
    user_text = "我不需要查天气，也暂时不用找餐厅。"
    plan = _plan(
        user_text,
        extraction=_extract(tasks=[
            RequestedTask(kind="WEATHER_CHECK", evidence="查天气"),
            RequestedTask(kind="FOOD_SEARCH", evidence="找餐厅"),
            # Neither assistant text nor a paraphrase absent from the user turn
            # is allowed to authorize a search.
            RequestedTask(kind="LODGING_SEARCH", evidence="帮我找酒店"),
        ]),
    )

    assert plan.weather_requested is False
    assert plan.preparation_requested is False
    assert plan.requested_category is None
    assert plan.search_requests == ()
