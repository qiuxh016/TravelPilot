from scripts.run_advisor_product_live_suite import (
    _aggregate_product_metrics,
    _proxy_budget_url,
    _scenario_summary,
    _validate_fresh_proxy_budget,
)


def test_proxy_budget_validation_rejects_used_wrong_origin_and_undersized():
    fresh = {
        "upstream_origin": "https://api.deepseek.com",
        "admitted_requests": 0,
        "requested_output_tokens": 0,
        "max_requests": 120,
        "max_requested_output_tokens": 9000,
        "max_output_tokens_per_request": 512,
    }
    _validate_fresh_proxy_budget(
        "http://127.0.0.1:8022/v1", fresh, aggregate_cap=10240, maximum_required=8704,
    )
    for changes in (
        {"admitted_requests": 1},
        {"upstream_origin": "https://attacker.invalid"},
        {"max_requested_output_tokens": 8000},
        {"max_requests": True},
    ):
        try:
            _validate_fresh_proxy_budget(
                "http://127.0.0.1:8022/v1",
                {**fresh, **changes},
                aggregate_cap=10240,
                maximum_required=8704,
            )
        except RuntimeError:
            continue
        raise AssertionError(f"invalid proxy budget was accepted: {changes}")


def test_proxy_budget_url_rejects_non_loopback_and_url_credentials():
    assert _proxy_budget_url("http://localhost:8022/v1") == "http://localhost:8022/budget"
    for url in (
        "http://remote.example:8022/v1",
        "http://user:pass@127.0.0.1:8022/v1",
    ):
        try:
            _proxy_budget_url(url)
        except RuntimeError:
            continue
        raise AssertionError(f"invalid proxy URL was accepted: {url}")


def test_scenario_summary_aggregates_trace_and_token_metrics():
    summary = _scenario_summary({
        "product_assessment": {"passed": False, "checks": {"safe": True}},
        "temporary_account_deleted": True,
        "resources": {"formal_write_prevented": True},
        "failure": None,
        "turns": [
            {
                "model_usage": {"input_tokens": 100, "output_tokens": 17},
                "request_latency_ms": 125.5,
                "actions": [
                    {"tool": "search_places", "status": "VERIFIED"},
                    {"tool": "get_trip_weather", "status": "FAILED"},
                ],
                "stage_statuses": [
                    {"stage": "SEARCH_WEATHER", "status": "FAILED"},
                ],
                "stage_decision_history": [
                    {"decision_source": "MODEL"},
                    {"decision_source": "RULE_RECOVERY"},
                ],
                "pending_question": "想先查哪一类地点？",
                "task_status": "WAITING_USER",
            },
            {
                "model_usage": {"input_tokens": 80, "output_tokens": 9},
                "request_latency_ms": 250,
                "actions": [{"tool": "build_schedule_draft", "status": "VERIFIED"}],
                "stage_statuses": [{"stage": "SCHEDULE", "status": "SUCCEEDED"}],
                "stage_decision_history": [{"decision_source": "MODEL"}],
                "pending_question": None,
                "task_status": "PROPOSED",
            },
        ],
    })

    assert summary["output_tokens"] == 26
    assert summary["input_tokens"] == 180
    assert summary["tool_action_count"] == 3
    assert summary["verified_tool_call_count"] == 2
    assert summary["failed_tool_call_count"] == 1
    assert summary["stage_failure_count"] == 1
    assert summary["stage_success_count"] == 1
    assert summary["model_stage_decision_count"] == 2
    assert summary["rule_recovery_decision_count"] == 1
    assert summary["clarification_turn_count"] == 1
    assert summary["formal_write_prevented"] is True
    assert summary["request_latency_ms_per_turn"] == [125.5, 250]


def test_scenario_summary_treats_missing_optional_usage_and_trace_as_zero():
    summary = _scenario_summary({
        "product_assessment": {"passed": True, "checks": {}},
        "temporary_account_deleted": True,
        "resources": {},
        "failure": None,
        "turns": [{"model_usage": None, "actions": None}],
    })

    assert summary["output_tokens"] == 0
    assert summary["input_tokens"] == 0
    assert summary["tool_action_count"] == 0
    assert summary["stage_success_count"] == 0
    assert summary["clarification_turn_count"] == 0
    assert summary["request_latency_ms_per_turn"] == []


def test_aggregate_metrics_reports_tool_and_decision_denominators():
    metrics = _aggregate_product_metrics([
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "tool_action_count": 4,
            "verified_tool_call_count": 3,
            "failed_tool_call_count": 1,
            "stage_success_count": 2,
            "stage_failure_count": 0,
            "model_stage_decision_count": 1,
            "rule_recovery_decision_count": 0,
            "clarification_turn_count": 1,
            "request_latency_ms_per_turn": [100, 300],
            "turn_count": 2,
        },
        {
            "input_tokens": 50,
            "output_tokens": 10,
            "tool_action_count": 0,
            "verified_tool_call_count": 0,
            "failed_tool_call_count": 0,
            "stage_success_count": 1,
            "stage_failure_count": 0,
            "model_stage_decision_count": 0,
            "rule_recovery_decision_count": 1,
            "clarification_turn_count": 0,
            "request_latency_ms_per_turn": [200],
            "turn_count": 1,
        },
    ])

    assert metrics["scenario_count"] == 2
    assert metrics["turn_count"] == 3
    assert metrics["input_tokens"] == 150
    assert metrics["output_tokens"] == 30
    assert metrics["tool_action_count"] == 4
    assert metrics["verified_tool_call_rate"] == 0.75
    assert metrics["model_stage_decision_count"] == 1
    assert metrics["rule_recovery_decision_count"] == 1
    assert metrics["latency_sample_count"] == 3
    assert metrics["mean_request_latency_ms"] == 200
    assert metrics["p95_request_latency_ms"] == 300


def test_aggregate_metrics_leaves_latency_empty_when_old_reports_have_no_samples():
    metrics = _aggregate_product_metrics([{"turn_count": 2}])

    assert metrics["latency_sample_count"] == 0
    assert metrics["mean_request_latency_ms"] is None
    assert metrics["p95_request_latency_ms"] is None
