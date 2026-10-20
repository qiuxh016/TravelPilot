import pytest
from travelpilot_api.advisor.turn_policy import (
    advisor_reply_source,
    can_compose_grounded_reply,
    grounded_general_trip_tip,
    grounded_reply_candidates,
    unsupported_booking_action,
)


def _can_compose(**overrides):
    values = {
        "schedule_recommendation_requested": False,
        "cancelled_pending_task": False,
        "invalidated_pending_task": False,
        "search_loop_question": None,
        "weather_result": None,
        "transport_result": None,
        "transport_route_requested": False,
        "requested_category": None,
        "preparation_requested": False,
        "verified_candidates_available": False,
        "unsupported_booking_requested": False,
    }
    return can_compose_grounded_reply(**(values | overrides))


def test_extraction_fallback_does_not_disable_read_only_open_consultation():
    # Extraction fallback is deliberately not a policy input: the composer sees
    # the original question and verified trip facts, but cannot execute tools.
    assert _can_compose()


def test_general_trip_fallback_uses_only_current_destination_and_saved_places():
    reply = grounded_general_trip_tip("成都", ["人民公园", "武侯祠", "人民公园"])

    assert reply is not None
    assert "成都" in reply
    assert "人民公园、武侯祠" in reply
    assert "开放时间" in reply
    assert "交通" in reply
    assert grounded_general_trip_tip("成都", []) is None
    assert grounded_general_trip_tip(None, ["人民公园"]) is None


@pytest.mark.parametrize(
    ("composed_reply", "model_generated_question", "expected"),
    [
        (True, False, "MODEL"),
        (False, True, "MODEL"),
        (False, False, "TEMPLATE"),
    ],
)
def test_reply_source_tracks_visible_reply_generation(
    composed_reply, model_generated_question, expected,
):
    assert advisor_reply_source(
        composed_reply=composed_reply,
        model_generated_question=model_generated_question,
    ) == expected


def test_scoped_workflows_keep_ownership_of_their_turn():
    assert not _can_compose(schedule_recommendation_requested=True)
    assert not _can_compose(search_loop_question="请补充旅行日期")
    assert not _can_compose(requested_category="FOOD")
    assert not _can_compose(transport_route_requested=True)


def test_verified_search_candidates_enable_grounded_synthesis_for_scoped_categories():
    assert _can_compose(requested_category="FOOD", verified_candidates_available=True)
    assert not _can_compose(requested_category="FOOD")
    assert not _can_compose(
        requested_category="FOOD", verified_candidates_available=True,
        unsupported_booking_requested=True,
    )
    assert not _can_compose(
        requested_category="FOOD", verified_candidates_available=True,
        schedule_recommendation_requested=True,
    )


def test_grounded_reply_reuses_only_matching_recent_category_and_meal_evidence():
    prior = [
        {"name": "博物馆", "planning_category": "ATTRACTION", "evidence_scope": "PRIOR_TURN"},
        {"name": "午餐店", "planning_category": "FOOD", "meal_type": "LUNCH", "evidence_scope": "PRIOR_TURN"},
        {"name": "晚餐店", "planning_category": "FOOD", "meal_type": "DINNER", "evidence_scope": "PRIOR_TURN"},
    ]
    result = grounded_reply_candidates(
        current_candidates=[], prior_candidates=prior, searched_requests=0,
        search_failures=[], requested_category="FOOD", requested_meal_type="LUNCH",
    )
    assert [item["name"] for item in result] == ["午餐店"]


def test_grounded_reply_does_not_restore_prior_candidates_after_failed_search():
    result = grounded_reply_candidates(
        current_candidates=[],
        prior_candidates=[{"name": "旧餐厅", "planning_category": "FOOD"}],
        searched_requests=1, search_failures=["PROVIDER_TIMEOUT"],
        requested_category="FOOD", requested_meal_type="LUNCH",
    )
    assert result == []


def test_explicit_external_booking_requests_are_stopped_before_tool_execution():
    cases = {
        "帮我直接订酒店": "LODGING_BOOKING_UNAVAILABLE",
        "预订这家民宿": "LODGING_BOOKING_UNAVAILABLE",
        "帮我订机票": "TICKET_BOOKING_UNAVAILABLE",
        "买门票": "TICKET_BOOKING_UNAVAILABLE",
        "帮我预约座位": "RESTAURANT_BOOKING_UNAVAILABLE",
    }
    for request, expected_code in cases.items():
        result = unsupported_booking_action(request)
        assert result is not None
        assert result[0] == expected_code
        assert "不会" in result[1] or "不能" in result[1]


def test_search_and_planning_requests_are_not_mistaken_for_external_booking():
    for request in ("推荐几家酒店", "找酒店候选", "帮我安排一个晚餐时间", "看看景点门票价格"):
        assert unsupported_booking_action(request) is None
