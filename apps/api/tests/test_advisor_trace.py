from travelpilot_api.advisor.trace import combine_agent_traces


def test_combined_turn_trace_keeps_each_executed_stage_and_transition_order():
    trace = combine_agent_traces([
        ("SEARCH_WEATHER", {
            "status": "SUCCEEDED", "decision_source": "MODEL",
            "decision_summary": "已核验天气和地点候选",
            "transitions": [
                {"tool": "get_trip_weather", "status": "VERIFIED"},
                {"tool": "search_places", "status": "VERIFIED"},
            ],
        }),
        ("ROUTE", {
            "status": "SUCCEEDED", "decision_source": "RULE",
            "decision_summary": "路线已核验",
            "transitions": [{"tool": "get_trip_route", "status": "VERIFIED"}],
        }),
        ("SCHEDULE", {
            "status": "NEEDS_USER_INPUT", "decision_source": "MODEL",
            "decision_summary": "排程需要确认",
            "transitions": [{"tool": "get_distance_matrix", "status": "VERIFIED"}],
        }),
    ])

    assert trace["primary_stage"] == "SEARCH_WEATHER"
    assert [item["stage"] for item in trace["stage_statuses"]] == [
        "SEARCH_WEATHER", "ROUTE", "SCHEDULE",
    ]
    assert trace["status"] == "NEEDS_USER_INPUT"
    assert [(item["stage"], item["tool"]) for item in trace["transitions"]] == [
        ("SEARCH_WEATHER", "get_trip_weather"),
        ("SEARCH_WEATHER", "search_places"),
        ("ROUTE", "get_trip_route"),
        ("SCHEDULE", "get_distance_matrix"),
    ]


def test_combined_turn_trace_handles_missing_optional_stages():
    trace = combine_agent_traces([
        ("SEARCH_WEATHER", None),
        ("ROUTE", {"status": "NEEDS_USER_INPUT", "transitions": []}),
    ])

    assert trace["primary_stage"] == "ROUTE"
    assert trace["stage_statuses"][0]["stage"] == "ROUTE"
    assert trace["transitions"] == []


def test_combined_turn_trace_is_absent_when_no_agent_loop_ran():
    assert combine_agent_traces([("SEARCH_WEATHER", None), ("SCHEDULE", None)]) is None
