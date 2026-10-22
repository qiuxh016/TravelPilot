from travelpilot_api.advisor.trace import combine_agent_traces


def test_combined_turn_trace_keeps_each_executed_stage_and_transition_order():
    trace = combine_agent_traces([
        ("SEARCH_WEATHER", {
            "status": "SUCCEEDED", "decision_source": "MODEL",
            "decision_summary": "已核验天气和地点候选",
            "transitions": [
                {"tool": "get_trip_weather", "status": "VERIFIED", "latency_ms": 12},
                {"tool": "search_places", "status": "VERIFIED", "latency_ms": 18},
            ],
            "execution_metrics": {"logical_steps": 2, "tool_attempts": 2, "retries": 1, "total_tokens": 40},
        }),
        ("ROUTE", {
            "status": "SUCCEEDED", "decision_source": "RULE",
            "decision_summary": "路线已核验",
            "transitions": [{"tool": "get_trip_route", "status": "VERIFIED", "latency_ms": 20}],
            "execution_metrics": {"logical_steps": 1, "tool_attempts": 1, "retries": 0, "total_tokens": 15},
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
    assert trace["execution_metrics"] == {
        "logical_steps": 3, "tool_attempts": 3, "retries": 1, "total_tokens": 55,
    }
    assert trace["tool_duration_ms"] == 50


def test_combined_trace_ignores_invalid_or_negative_metrics():
    trace = combine_agent_traces([("SEARCH_WEATHER", {
        "status": "SUCCEEDED", "execution_metrics": {
            "tool_attempts": 2, "output_tokens": -1, "input_tokens": True,
            "unknown": 999,
        },
        "transitions": [{"tool": "search_places", "status": "VERIFIED", "latency_ms": -5}],
    })])
    assert trace["execution_metrics"] == {"tool_attempts": 2}
    assert trace["tool_duration_ms"] == 0


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
