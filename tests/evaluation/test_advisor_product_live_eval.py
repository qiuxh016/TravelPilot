from datetime import date, datetime, timedelta

import httpx
import pytest

from tests.evaluation import advisor_product_live_eval as evaluation


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8002/v1", "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"),
        ("http://localhost:8002/v1", "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"),
        ("http://[::1]:8002/v1", "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"),
        ("https://api.deepseek.com/v1", "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS"),
    ],
)
def test_provider_mode_distinguishes_loopback_model_stubs(base_url, expected):
    assert evaluation._provider_mode(base_url) == expected


def test_explicit_local_stub_mode_overrides_parent_dotenv_provider_metadata():
    assert evaluation._provider_mode(
        "https://api.deepseek.com/v1", model_is_local_stub=True,
    ) == "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS"


def test_local_stub_report_never_inherits_parent_dotenv_model_name():
    assert evaluation._reported_model_name(
        "LOCAL_MODEL_STUB_REAL_ADVISOR_API_FAKE_MAPS",
        effective_server_token_caps=None,
        configured_model="deepseek-flash",
    ) == "local-stub"


def test_live_model_report_uses_authenticated_api_model_status():
    assert evaluation._reported_model_name(
        "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS",
        effective_server_token_caps={
            "model_name": "deepseek-chat",
            "output_token_limit": 128,
            "session_output_token_limit": 256,
        },
        configured_model="deepseek-flash",
    ) == "deepseek-chat"


def test_product_assessment_requires_real_proposal_verified_tools_and_safe_cleanup():
    turns = [
        {"http_status": 200, "proposal_present": False, "actions": []},
        {
            "http_status": 200,
            "proposal_present": True,
            "trip_context": {"saved_places": ["人民公园", "武侯祠", "锦里"]},
            "schedule_suggestion_count": 3,
            "actions": [
                {"tool": "get_distance_matrix", "status": "VERIFIED"},
                {"tool": "build_schedule_draft", "status": "VERIFIED"},
            ],
        },
    ]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(turns, resources, True)

    assert result["passed"] is True
    assert all(result["checks"].values())
    assert evaluation._assess_product_outcome(turns, resources, False)["passed"] is False
    turns[1]["actions"][-1]["status"] = "FAILED"
    assert evaluation._assess_product_outcome(turns, resources, True)["passed"] is False
    turns[1]["actions"][-1]["status"] = "VERIFIED"
    turns[1]["schedule_suggestion_count"] = 2
    assert evaluation._assess_product_outcome(turns, resources, True)["passed"] is False


def test_lunch_product_assessment_rejects_non_food_or_unlabeled_candidates():
    turns = [{
        "http_status": 200,
        "proposal_present": False,
        "actions": [{"tool": "search_places", "status": "VERIFIED"}],
        "verified_candidates": [{
            "planning_category": "FOOD", "meal_type": "LUNCH",
        }],
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(turns, resources, True, "lunch-only")

    assert result["checks"]["food_candidates_present"] is True
    assert result["checks"]["all_candidates_are_food"] is True
    assert result["checks"]["candidates_labeled_as_lunch"] is True
    assert result["checks"]["no_itinerary_schedule_action"] is True
    turns[0]["verified_candidates"][0]["planning_category"] = "ATTRACTION"
    assert evaluation._assess_product_outcome(turns, resources, True, "lunch-only")["passed"] is False


def test_lunch_weather_product_assessment_requires_both_grounded_domains_without_itinerary():
    turns = [{
        "http_status": 200,
        "assistant_reply": "成都午餐候选。预报有小雨，建议带伞或轻便雨衣。",
        "proposal_present": False,
        "draft_present": False,
        "actions": [
            {"tool": "search_places", "status": "VERIFIED"},
            {"tool": "get_trip_weather", "status": "VERIFIED"},
        ],
        "verified_candidates": [
            {"planning_category": "FOOD", "meal_type": "LUNCH"},
            {"planning_category": "FOOD", "meal_type": "LUNCH"},
        ],
        "weather_evidence": {
            "source": "Local simulated forecast",
            "status": "AVAILABLE",
            "days": [{"date": "2026-10-20", "weather_code": 61}],
        },
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-preparation",
    )
    assert result["passed"] is True

    turns[0]["assistant_reply"] += " 降水可能性偏高，建议带伞或轻便雨衣。"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-preparation",
    )["checks"]["identical_daily_rain_advice_is_not_repeated"] is False
    turns[0]["assistant_reply"] = "成都午餐候选。预报有小雨，建议带伞或轻便雨衣。"
    turns[0]["verified_candidates"][0]["meal_type"] = None
    assert evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-preparation",
    )["checks"]["candidates_are_food_and_labeled_lunch"] is False
    turns[0]["verified_candidates"][0]["meal_type"] = "LUNCH"
    turns[0]["actions"].pop()
    assert evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-preparation",
    )["checks"]["meal_search_and_weather_tools_verified"] is False


def test_lunch_weather_outage_keeps_food_results_and_rejects_invented_forecast():
    turns = [{
        "http_status": 200,
        "assistant_reply": "找到午餐候选。天气服务暂时不可用，本轮没有生成天气结论。",
        "proposal_present": False,
        "draft_present": False,
        "actions": [
            {"tool": "get_trip_weather", "status": "FAILED", "error": "WEATHER_PROVIDER_UNAVAILABLE"},
            {"tool": "search_places", "status": "VERIFIED"},
        ],
        "verified_candidates": [
            {"planning_category": "FOOD", "meal_type": "LUNCH"},
        ],
        "weather_evidence": {"status": "UNAVAILABLE", "days": []},
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-weather-outage",
    )
    assert result["passed"] is True
    turns[0]["weather_evidence"]["days"] = [{"date": "2026-10-20", "weather_code": 61}]
    assert evaluation._assess_product_outcome(
        turns, resources, True, "lunch-weather-weather-outage",
    )["checks"]["weather_failure_is_disclosed_without_invented_forecast"] is False


def test_mixed_discovery_requires_both_domains_and_keeps_meal_type():
    turns = [{
        "http_status": 200,
        "proposal_present": False,
        "draft_present": False,
        "actions": [{"tool": "search_places", "status": "VERIFIED"}],
        "verified_candidates": [
            {"planning_category": "ATTRACTION", "verification_status": "VERIFIED"},
            {"planning_category": "FOOD", "meal_type": "LUNCH", "verification_status": "SIMULATED"},
        ],
    }]
    result = evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "mixed-attraction-lunch",
    )

    assert result["passed"] is True
    turns[0]["verified_candidates"].pop()
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "mixed-attraction-lunch",
    )["checks"]["both_requested_domains_have_verified_candidates"] is False


def test_mixed_domain_draft_requires_user_selected_lunch_time_and_no_formal_write():
    turns = [{
        "http_status": 200,
        "verified_candidates": [
            {"planning_category": "ATTRACTION", "verification_status": "VERIFIED"},
            {"planning_category": "FOOD", "meal_type": "LUNCH", "verification_status": "VERIFIED"},
        ],
    }]
    resources = {
        "formal_write_prevented": True,
        "formal_plan_count_before": 0,
        "formal_plan_count_after": 0,
        "draft_preview": {"stops": [
            {"planning_category": "ATTRACTION", "meal_type": None, "start_time": "09:00:00"},
            {"planning_category": "FOOD", "meal_type": "LUNCH", "start_time": "12:00:00"},
        ]},
    }

    result = evaluation._assess_product_outcome(
        turns, resources, True, "mixed-attraction-lunch-draft",
    )

    assert result["passed"] is True
    resources["draft_preview"]["stops"][1]["start_time"] = "16:00:00"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "mixed-attraction-lunch-draft",
    )["checks"]["lunch_slot_and_time_are_preserved"] is False


def test_lodging_product_assessment_keeps_hotel_candidates_out_of_itinerary():
    turns = [{
        "http_status": 200,
        "proposal_present": False,
        "actions": [{"tool": "search_places", "status": "VERIFIED"}],
        "verified_candidates": [{
            "name": "模拟旅行酒店", "planning_category": "LODGING",
        }],
    }]
    result = evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "lodging-only",
    )

    assert result["passed"] is True
    assert all(result["checks"].values())

    turns[0]["verified_candidates"].append({
        "name": "宽窄巷子", "planning_category": "ATTRACTION",
    })
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "lodging-only",
    )["passed"] is False


def test_free_city_discovery_requires_multiple_verified_amap_searches_and_no_trip_write():
    turns = [{
        "http_status": 200,
        "event": "DESTINATION_RECOMMENDATIONS",
        "trip_context": {"map_evidence": "AMap Web Service"},
        "destination_recommendations": [{
            "city": "成都", "provider": "amap",
            "places": [{"name": "成都公园", "provider": "amap", "provider_id": "p1"}],
        }],
        "destination_search_count": 2,
        "actions": [
            {"tool": "search_destination_places", "status": "VERIFIED"},
            {"tool": "search_destination_places", "status": "VERIFIED"},
        ],
    }]
    result = evaluation._assess_product_outcome(
        turns,
        {"trip_created": False, "formal_write_prevented": True},
        True,
        "destination-discovery",
    )

    assert result["passed"] is True
    turns[0]["destination_recommendations"][0]["places"][0]["provider"] = "fake"
    assert evaluation._assess_product_outcome(
        turns,
        {"trip_created": False, "formal_write_prevented": True},
        True,
        "destination-discovery",
    )["checks"]["amap_evidence_is_present"] is False


def test_destination_discovery_scenario_uses_free_exploration_context():
    scenario = evaluation.SCENARIOS["destination-discovery"]

    assert scenario["advisor_mode"] == "discover"
    assert scenario["saved_places"] == ()
    assert scenario["maps_data_mode"] == "LOCAL_AMAP_FIXTURE"


def test_route_weather_outage_assessment_preserves_route_and_discloses_missing_forecast():
    turns = [{
        "http_status": 200,
        "assistant_reply": "已核验人民公园到武侯祠的驾车路线。天气服务暂时不可用。",
        "weather_evidence": {},
        "route_evidence": {
            "origin_name": "人民公园",
            "destination_name": "武侯祠",
        },
        "proposal_present": False,
        "draft_present": False,
        "actions": [
            {"tool": "get_trip_route", "status": "VERIFIED"},
            {
                "tool": "get_trip_weather",
                "status": "FAILED",
                "error": "WEATHER_PROVIDER_UNAVAILABLE",
            },
        ],
    }]

    result = evaluation._assess_product_outcome(
        turns,
        {"formal_write_prevented": True},
        True,
        "route-weather-weather-outage",
    )

    assert result["passed"] is True
    assert all(result["checks"].values())
    turns[0]["assistant_reply"] = "已核验人民公园到武侯祠的驾车路线，今天晴天。"
    assert evaluation._assess_product_outcome(
        turns,
        {"formal_write_prevented": True},
        True,
        "route-weather-weather-outage",
    )["passed"] is False


def test_schedule_proposal_edit_assessment_requires_safe_update_without_search():
    turns = [{
        "http_status": 200,
        "proposal_present": True,
        "assistant_reply": (
            "已按草案安排：第1天（1月27日）：人民公园 09:00、武侯祠 13:30；"
            "第2天（1月28日）：锦里 13:00。这是一份待确认草案；午餐已预留，未安排餐厅。"
        ),
        "trip_context": {
            "start_date": "2027-01-27",
            "end_date": "2027-01-28",
            "saved_places": ["人民公园", "武侯祠", "锦里"],
        },
        "schedule_assignments": [
            {"day": "2027-01-27", "start_time": "09:00"},
            {"day": "2027-01-27", "start_time": "13:30"},
            {"day": "2027-01-28", "start_time": "13:00"},
        ],
        "schedule_breaks": [
            {
                "day": day,
                "meal_type": "LUNCH",
                "start_time": "12:00",
                "end_time": "13:00",
                "duration_min": 60,
                "time_is_proposed_default": True,
            }
            for day in ("2027-01-27", "2027-01-28")
        ],
        "actions": [
            {"tool": "build_schedule_draft", "status": "VERIFIED"},
        ],
        "draft_follow_up_options": [{
            "key": "DRAFT_MEAL:LUNCH", "title": "草案里的午餐还没安排",
            "action_label": "推荐午餐",
        }],
    }]
    result = evaluation._assess_product_outcome(
        turns,
        {"formal_write_prevented": True},
        True,
        "schedule-proposal-edit",
    )
    assert result["passed"] is True
    assert result["checks"]["reply_summarizes_assigned_places_dates_and_times"] is True
    turns[0]["assistant_reply"] = "我已生成待确认草案，请到右侧查看。"
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "schedule-proposal-edit",
    )["checks"]["reply_summarizes_assigned_places_dates_and_times"] is False
    turns[0]["assistant_reply"] = (
        "已按草案安排：第1天（1月27日）：人民公园 09:00、武侯祠 13:30；"
        "第2天（1月28日）：锦里 13:00。这是一份待确认草案；午餐已预留，未安排餐厅。"
    )
    turns[0]["actions"].append({"tool": "search_places", "status": "VERIFIED"})
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "schedule-proposal-edit",
    )["checks"]["edit_does_not_trigger_unrequested_place_search"] is False
    turns[0]["trip_context"].pop("start_date")
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "schedule-proposal-edit",
    )["checks"]["second_morning_remains_free"] is False


def test_schedule_assessment_accepts_route_matrix_evidence_from_prior_turn():
    turns = [
        {
            "http_status": 200,
            "actions": [{"tool": "get_distance_matrix", "status": "VERIFIED"}],
        },
        {
            "http_status": 200,
            "proposal_present": True,
            "schedule_suggestion_count": 3,
            "trip_context": {"saved_places": ["人民公园", "武侯祠", "锦里"]},
            "actions": [{"tool": "build_schedule_draft", "status": "VERIFIED"}],
        },
    ]
    result = evaluation._assess_product_outcome(
        turns,
        {"formal_write_prevented": True},
        True,
        "schedule-two-turn",
    )
    assert result["checks"]["route_matrix_verified"] is True
    assert result["passed"] is True


def test_turn_metrics_extract_only_bounded_agent_and_draft_metadata():
    session = {
        "messages": [{
            "role": "assistant", "content": "reply",
            "payload": {"agent_task": {"status": "PROPOSED", "turn_number": 2},
                        "action": "SCHEDULE_PROPOSAL",
                        "response_source": "MODEL",
                        "response_fallback_reason": "INVALID_JSON_OR_SCHEMA",
                        "extraction_source": "MODEL",
                        "schedule_suggestions": [{"candidate_id": "private-id"}],
                        "draft_follow_up_options": [{
                            "key": "DRAFT_MEAL:LUNCH", "title": "草案里的午餐还没安排",
                            "action_label": "推荐午餐", "prompt": "private prompt text",
                        }],
                        "verified_candidates": [{
                            "name": "模拟川味餐馆", "planning_category": "FOOD",
                            "meal_type": "LUNCH", "verification_status": "VERIFIED",
                            "private": "not exposed",
                        }],
                        "weather_forecast": {
                            "source": "Local simulated forecast",
                            "status": "AVAILABLE",
                            "fetched_at": "2026-10-19T08:00:00+00:00",
                            "days": [{
                                "date": "2027-01-27", "description": "小雨（模拟）",
                                "weather_code": 61, "precipitation_probability_max": 75,
                                "advice": [{"kind": "RAIN", "text": "建议带伞"}],
                                "coordinates": [30.6, 104.0],
                            }],
                            "raw_provider_payload": {"private": "not exposed"},
                        },
                        "agent_trace": {"transitions": [
                            {"kind": "CALL_TOOL", "tool": "get_distance_matrix", "status": "SUCCEEDED",
                             "raw_provider_payload": {"sensitive": "not exposed"}},
                            {"summary": "explain" * 1000},
                        ]}},
        }],
        "context": {"advisor_llm_usage": {
            "input_tokens": 300, "output_tokens": 30, "total_tokens": 330,
        }},
        "draft": {"candidate_ids": ["fixture"]}, "draft_version": 1,
    }

    result = evaluation._turn_metrics(
        session,
        200,
        user_message="用户提出的合成任务",
        previous_model_usage={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
        trip_context={
            "destination": "成都", "start_date": "2027-01-27",
            "end_date": "2027-01-28", "pace_level": 2,
            "saved_places": ["人民公园", "武侯祠", "锦里"],
        },
    )

    assert result["task_status"] == "PROPOSED"
    assert result["task_turn_number"] == 2
    assert result["proposal_present"] is True
    assert result["response_fallback_reason"] == "INVALID_JSON_OR_SCHEMA"
    assert result["response_source"] == "MODEL"
    assert result["model_usage"] == {
        "input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
    }
    assert result["model_usage_session_total"] == {
        "input_tokens": 300, "output_tokens": 30, "total_tokens": 330,
    }
    assert result["schedule_suggestion_count"] == 1
    assert result["schedule_assignments"] == [{}]
    assert "private-id" not in str(result["schedule_assignments"])
    assert result["draft_follow_up_options"] == [{
        "key": "DRAFT_MEAL:LUNCH", "title": "草案里的午餐还没安排",
        "action_label": "推荐午餐",
    }]
    assert "private prompt text" not in str(result)
    assert result["verified_candidates"] == [{
        "name": "模拟川味餐馆", "planning_category": "FOOD", "meal_type": "LUNCH",
        "verification_status": "VERIFIED",
    }]
    assert result["weather_evidence"] == {
        "status": "AVAILABLE",
        "source": "Local simulated forecast",
        "fetched_at": "2026-10-19T08:00:00+00:00",
        "days": [{
            "date": "2027-01-27", "description": "小雨（模拟）",
            "weather_code": 61, "precipitation_probability_max": 75,
            "advice": ["建议带伞"],
        }],
    }
    assert result["actions"] == [{
        "kind": "CALL_TOOL", "tool": "get_distance_matrix", "status": "SUCCEEDED",
    }]
    assert result["user_message"] == "用户提出的合成任务"
    assert result["trip_context"]["saved_places"] == ["人民公园", "武侯祠", "锦里"]
    assert result["assistant_reply"] == "reply"
    assert result["assistant_reply_complete"] is True
    assert "raw_provider_payload" not in str(result)
    assert "coordinates" not in str(result["weather_evidence"])
    assert len(result["actions"]) == 1


def test_turn_metrics_marks_reply_truncation_for_review_safety():
    session = {"messages": [{"role": "assistant", "content": "x" * 1601}]}

    result = evaluation._turn_metrics(
        session,
        200,
        user_message="合成提问",
        trip_context={"destination": "成都", "saved_places": []},
    )

    assert len(result["assistant_reply"]) == 1600
    assert result["assistant_reply_complete"] is False


def test_live_product_inputs_are_synthetic_and_do_not_request_confirmation():
    assert len(evaluation.TURN_INPUTS) == 2
    assert "不要替我确认保存" in evaluation.TURN_INPUTS[1]
    assert evaluation.DEFAULT_OUTPUT.name.endswith("20261019.json")


def test_weather_indoor_scenario_requires_ordered_labelled_fixture_and_no_write():
    turns = [{
        "http_status": 200,
        "proposal_present": False,
        "actions": [
            {"tool": "get_trip_weather", "status": "VERIFIED"},
            {"tool": "search_places", "status": "VERIFIED"},
        ],
        "weather_evidence": {
            "source": "Local simulated forecast",
            "days": [{"weather_code": 61}],
        },
        "verified_candidates": [{
            "name": "模拟室内展馆",
            "verification_status": "SIMULATED",
        }],
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "weather-indoor-search",
    )

    assert result["passed"] is True
    assert result["checks"]["weather_observed_before_indoor_search"] is True


def test_weather_indoor_scenario_rejects_unlabelled_or_live_looking_fixture():
    turns = [{
        "http_status": 200,
        "proposal_present": False,
        "actions": [
            {"tool": "search_places", "status": "VERIFIED"},
            {"tool": "get_trip_weather", "status": "VERIFIED"},
        ],
        "weather_evidence": {
            "source": "Open-Meteo",
            "days": [{"weather_code": 61}],
        },
        "verified_candidates": [{
            "name": "模拟室内展馆",
            "verification_status": "VERIFIED",
        }],
    }]

    result = evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "weather-indoor-search",
    )

    assert result["passed"] is False
    assert result["checks"]["weather_observed_before_indoor_search"] is False
    assert result["checks"]["synthetic_rain_evidence_is_source_labelled"] is False
    assert result["checks"]["indoor_search_returns_simulated_candidate"] is False


def test_weather_preparation_requires_dated_forecast_grounded_advice_and_no_mutation():
    turns = [{
        "http_status": 200,
        "assistant_reply": "预报有小雨，建议带伞或轻便雨衣。",
        "actions": [{"tool": "get_trip_weather", "status": "VERIFIED"}],
        "weather_evidence": {
            "status": "AVAILABLE",
            "source": "Local simulated forecast",
            "days": [{"date": "2027-01-27", "weather_code": 61}],
        },
        "proposal_present": False,
        "draft_present": False,
    }]

    result = evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "weather-preparation",
    )

    assert result["passed"] is True

    turns[0]["assistant_reply"] = "天气很好，不需要带伞。"
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "weather-preparation",
    )["checks"]["rain_advice_is_grounded_in_forecast"] is False


def test_compound_route_weather_assessment_requires_both_verified_stages():
    turns = [{
        "http_status": 200,
        "assistant_reply": "人民公园到武侯祠可驾车前往；预报有雨，建议带伞或轻便雨衣。",
        "actions": [
            {"tool": "get_trip_route", "status": "VERIFIED"},
            {"tool": "get_trip_weather", "status": "VERIFIED"},
        ],
        "stage_statuses": [
            {"stage": "ROUTE", "status": "SUCCEEDED"},
            {"stage": "SEARCH_WEATHER", "status": "SUCCEEDED"},
        ],
        "route_evidence": {
            "origin_name": "人民公园",
            "destination_name": "武侯祠",
            "mode": "DRIVING",
            "duration_min": 15,
        },
        "weather_evidence": {
            "source": "Local simulated forecast",
            "days": [{"date": "2027-01-27", "weather_code": 61}],
        },
        "proposal_present": False,
        "draft_present": False,
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "route-weather-preparation",
    )

    assert result["passed"] is True
    turns[0]["assistant_reply"] = "成都天气有雨，建议带伞或轻便雨衣。"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-weather-preparation",
    )["checks"]["reply_combines_route_and_weather_preparation"] is False
    turns[0]["assistant_reply"] = "人民公园到武侯祠可驾车前往；预报有雨，建议带伞或轻便雨衣。"
    turns[0]["stage_statuses"][1]["status"] = "FAILED"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-weather-preparation",
    )["checks"]["route_and_weather_stages_completed"] is False


def test_compound_weather_assessment_requires_disclosing_route_outage():
    turns = [{
        "http_status": 200,
        "assistant_reply": "成都预报有雨，建议带伞或轻便雨衣。路线本轮未能核验。",
        "actions": [
            {
                "tool": "get_trip_route", "status": "FAILED",
                "error": "MAPS_PROVIDER_UNAVAILABLE",
            },
            {"tool": "get_trip_weather", "status": "VERIFIED"},
        ],
        "weather_evidence": {
            "source": "Local simulated forecast",
            "days": [{"date": "2027-01-27", "weather_code": 61}],
        },
        "proposal_present": False,
        "draft_present": False,
    }]
    resources = {"formal_write_prevented": True}

    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-weather-route-outage",
    )["passed"] is True

    turns[0]["assistant_reply"] = "成都预报有雨，建议带伞或轻便雨衣。"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-weather-route-outage",
    )["checks"]["route_failure_is_disclosed"] is False


def test_unsupported_intercity_request_keeps_weather_and_never_assumes_driving():
    turns = [{
        "http_status": 200,
        "assistant_reply": "成都预报有雨，建议带伞。",
        "pending_question": (
            "跨城铁路和航班查询尚未接入，"
            "我无法核验班次、时刻或票价。如果你想查询行程中两处已保存地点之间的路线，"
            "请告诉我起点、终点和出行方式；我不会自动改成驾车路线。"
        ),
        "actions": [{"tool": "get_trip_weather", "status": "VERIFIED"}],
        "weather_evidence": {
            "status": "AVAILABLE",
            "source": "Local simulated forecast",
            "days": [{"date": "2026-10-22", "weather_code": 61}],
        },
        "proposal_present": False,
        "draft_present": False,
    }]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "unsupported-intercity-weather",
    )

    assert result["passed"] is True
    assert all(result["checks"].values())
    turns[0]["pending_question"] = "要不要我给你查询驾车路线？"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "unsupported-intercity-weather",
    )["checks"]["no_automatic_driving_substitution"] is False


def test_general_trip_question_requires_grounding_and_no_plan_side_effects():
    turns = [{
        "http_status": 200,
        "assistant_reply_present": True,
        "assistant_reply": "这段成都旅行目前收藏了人民公园、武侯祠。建议预留休息和用餐时间。",
        "response_source": "MODEL",
        "trip_context": {"saved_places": ["人民公园", "武侯祠"]},
        "action_count": 0,
        "proposal_present": False,
        "draft_present": False,
    }]

    result = evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "general-trip-question",
    )

    assert result["passed"] is True
    assert all(result["checks"].values())

    turns[0]["assistant_reply"] = "欢迎去成都旅行。"
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "general-trip-question",
    )["checks"]["reply_mentions_a_saved_place"] is False

    turns[0]["assistant_reply"] = "这段成都旅行目前收藏了人民公园。"
    turns[0]["action_count"] = 1
    assert evaluation._assess_product_outcome(
        turns, {"formal_write_prevented": True}, True, "general-trip-question",
    )["checks"]["no_search_or_plan_mutation"] is False


def test_general_trip_question_is_a_read_only_product_scenario():
    scenario = evaluation.SCENARIOS["general-trip-question"]

    assert len(scenario["turn_inputs"]) == 1
    assert len(scenario["saved_places"]) == 2
    assert "不要替我确认保存" not in scenario["turn_inputs"][0]


def test_schedule_route_weather_scenario_requests_a_compound_trip_outcome():
    scenario = evaluation.SCENARIOS["schedule-route-weather"]

    assert len(scenario["turn_inputs"]) == 2
    assert {item[0] for item in scenario["saved_places"]} >= {
        "人民公园", "武侯祠", "锦里",
    }
    request = scenario["turn_inputs"][0]
    assert "安排" in request and "路线" in request and "天气" in request
    assert "三处都安排" in scenario["turn_inputs"][1]
    assert scenario["weather_data_mode"] == "LOCAL_SIMULATED_FIXTURE"


def test_route_provider_outage_requires_warning_before_unconfirmed_fallback():
    turns = [
        {"http_status": 200, "actions": [], "proposal_present": False},
        {
            "http_status": 200,
            "assistant_reply": "路线暂不可用，我用本地距离估算辅助排程（路线未核验）。",
            "actions": [
                {"tool": "get_distance_matrix", "status": "FAILED", "error": "MAPS_PROVIDER_UNAVAILABLE"},
                {"tool": "build_schedule_draft", "status": "VERIFIED"},
            ],
            "proposal_present": True,
            "draft_present": False,
        },
    ]
    resources = {"formal_write_prevented": True}

    result = evaluation._assess_product_outcome(
        turns, resources, True, "route-provider-outage",
    )

    assert result["passed"] is True
    assert all(result["checks"].values())

    turns[1]["assistant_reply"] = "已核验全部路线并完成排程。"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-provider-outage",
    )["checks"]["route_estimate_limit_is_disclosed"] is False

    turns[1]["assistant_reply"] = "路线暂不可用，我用本地距离估算辅助排程（路线未核验）。"
    turns[1]["actions"][0]["status"] = "VERIFIED"
    assert evaluation._assess_product_outcome(
        turns, resources, True, "route-provider-outage",
    )["checks"]["route_failure_is_visible_in_trace"] is False


def test_report_distinguishes_server_caps_from_runner_environment():
    import inspect

    parameters = inspect.signature(evaluation.run_eval).parameters
    assert "server_output_cap" in parameters
    assert "server_session_cap" in parameters


def test_server_token_cap_handshake_rejects_missing_or_over_budget_limits():
    request = httpx.Request("GET", "http://localhost/api/v1/advisor/status")

    accepted = evaluation._verify_server_token_caps(
        httpx.Response(
            200,
            json={
                "llm_status": "CONFIGURED", "model_name": "test-model",
                "output_token_limit": 128, "session_output_token_limit": 512,
            },
            request=request,
        ),
        max_output_tokens_per_turn=128,
        max_output_tokens_per_session=512,
        expected_model="test-model",
    )
    assert accepted == {
        "model_name": "test-model",
        "output_token_limit": 128,
        "session_output_token_limit": 512,
    }

    with pytest.raises(RuntimeError, match="ADVISOR_PER_TURN_TOKEN_CAP_EXCEEDS_AUTHORIZED_LIMIT"):
        evaluation._verify_server_token_caps(
            httpx.Response(
                200,
                json={
                    "llm_status": "CONFIGURED", "model_name": "test-model",
                    "output_token_limit": 129, "session_output_token_limit": 512,
                },
                request=request,
            ),
            max_output_tokens_per_turn=128,
            max_output_tokens_per_session=512,
            expected_model="test-model",
        )
    with pytest.raises(RuntimeError, match="ADVISOR_SESSION_TOKEN_CAP_EXCEEDS_AUTHORIZED_LIMIT"):
        evaluation._verify_server_token_caps(
            httpx.Response(
                200,
                json={
                    "llm_status": "CONFIGURED", "model_name": "test-model",
                    "output_token_limit": 128, "session_output_token_limit": 513,
                },
                request=request,
            ),
            max_output_tokens_per_turn=128,
            max_output_tokens_per_session=512,
            expected_model="test-model",
        )
    with pytest.raises(RuntimeError, match="ADVISOR_STATUS_TOKEN_CAPS_MISSING"):
        evaluation._verify_server_token_caps(
            httpx.Response(200, json={
                "llm_status": "CONFIGURED", "model_name": "test-model",
                "output_token_limit": 128,
            }, request=request),
            max_output_tokens_per_turn=128,
            max_output_tokens_per_session=512,
            expected_model="test-model",
        )
    with pytest.raises(RuntimeError, match="ADVISOR_MODEL_NOT_CONFIGURED"):
        evaluation._verify_server_token_caps(
            httpx.Response(200, json={
                "llm_status": "NOT_CONFIGURED", "model_name": None,
                "output_token_limit": 128, "session_output_token_limit": 512,
            }, request=request),
            max_output_tokens_per_turn=128,
            max_output_tokens_per_session=512,
        )
    with pytest.raises(RuntimeError, match="ADVISOR_MODEL_MISMATCH"):
        evaluation._verify_server_token_caps(
            httpx.Response(200, json={
                "llm_status": "CONFIGURED", "model_name": "other-model",
                "output_token_limit": 128, "session_output_token_limit": 512,
            }, request=request),
            max_output_tokens_per_turn=128,
            max_output_tokens_per_session=512,
            expected_model="test-model",
        )


def test_live_product_run_requires_explicit_server_caps_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(
        evaluation, "settings",
        type("Settings", (), {
            "llm_base_url": "https://api.deepseek.com/v1",
            "llm_model": "deepseek-flash",
            "llm_max_tokens_per_turn": 1200,
            "llm_max_output_tokens_per_session": 12000,
        })(),
    )

    async def run():
        await evaluation.run_eval(
            base_url="http://127.0.0.1:8015",
            output=tmp_path / "live-no-caps.json",
        )

    with pytest.raises(ValueError, match="requires explicit per-turn and session output caps"):
        import asyncio

        asyncio.run(run())


def test_frozen_trip_dates_are_reused_for_paired_runs():
    start = date(2027, 1, 27)

    assert evaluation._resolve_trip_dates(start) == (start, date(2027, 1, 28))


def test_default_trip_dates_stay_inside_standard_weather_forecast_horizon():
    start, end = evaluation._resolve_trip_dates(None)

    assert start == datetime.now().astimezone().date() + timedelta(days=3)
    assert end == start + timedelta(days=1)


def test_product_eval_stops_when_turn_usage_exceeds_approved_cap():
    evaluation._validate_turn_output_cap({"model_usage": {"output_tokens": 128}}, 128)

    with pytest.raises(RuntimeError, match="ADVISOR_TURN_OUTPUT_BUDGET_EXCEEDED"):
        evaluation._validate_turn_output_cap(
            {"model_usage": {"output_tokens": 132}}, 128,
        )


def test_live_evaluation_rejects_past_trip_date_before_http_requests(tmp_path):
    with pytest.raises(ValueError, match="must be in the future"):
        evaluation._resolve_trip_dates(datetime.now().astimezone().date() - timedelta(days=1))


@pytest.mark.asyncio
async def test_live_evaluation_never_overwrites_an_existing_report(tmp_path):
    report = tmp_path / "existing.json"
    report.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        await evaluation.run_eval(
            base_url="http://127.0.0.1:8015",
            output=report,
            trip_start_date=datetime.now().astimezone().date() - timedelta(days=1),
        )

    assert report.read_text(encoding="utf-8") == "preserve"


@pytest.mark.asyncio
async def test_live_evaluation_rejects_past_trip_date_before_network_or_output(tmp_path):
    output = tmp_path / "not-created.json"

    with pytest.raises(ValueError, match="must be in the future"):
        await evaluation.run_eval(
            base_url="http://127.0.0.1:8015",
            output=output,
            trip_start_date=datetime.now().astimezone().date() - timedelta(days=1),
        )

    assert not output.exists()


@pytest.mark.parametrize(
    "status, header, expected_error",
    [
        (200, "expected", None),
        (200, "stale", "ADVISOR_RUNTIME_FINGERPRINT_MISMATCH"),
        (200, None, "ADVISOR_RUNTIME_FINGERPRINT_MISMATCH"),
        (503, "expected", "API_HEALTH_HTTP_503"),
    ],
)
def test_health_runtime_handshake(status, header, expected_error):
    request = httpx.Request("GET", "http://localhost/health")
    response = httpx.Response(
        status,
        headers={"X-Advisor-Runtime-Fingerprint": header} if header else {},
        request=request,
    )

    if expected_error:
        with pytest.raises(RuntimeError, match=expected_error):
            evaluation._verify_api_runtime(response, "expected")
    else:
        assert evaluation._verify_api_runtime(response, "expected") == "expected"


@pytest.mark.asyncio
async def test_runtime_mismatch_fails_before_temporary_account_registration(monkeypatch, tmp_path):
    class ClientStub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _path):
            return httpx.Response(
                200,
                headers={"X-Advisor-Runtime-Fingerprint": "stale"},
                request=httpx.Request("GET", "http://localhost/health"),
            )

        async def post(self, *_args, **_kwargs):
            pytest.fail("registration must not happen before runtime verification")

    monkeypatch.setattr(evaluation.httpx, "AsyncClient", lambda **_kwargs: ClientStub())
    output = tmp_path / "runtime-mismatch.json"

    with pytest.raises(RuntimeError, match="ADVISOR_RUNTIME_FINGERPRINT_MISMATCH"):
        await evaluation.run_eval(
            base_url="http://127.0.0.1:8015",
            output=output,
            trip_start_date=datetime.now().astimezone().date() + timedelta(days=100),
            server_output_cap=128,
            server_session_cap=512,
        )

    assert not output.exists()
