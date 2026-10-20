import asyncio

import pytest
from travelpilot_api.advisor.stage_execution import (
    AdvisorTurnExecutor,
    execute_ready_stage_queue,
    summarize_stage_execution,
)
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.turn_plan import (
    AdvisorTurnPlan,
    AdvisorTurnStage,
)


def _stage(name, depends_on=()):
    return AdvisorTurnStage(
        name=name,
        task_context=build_agent_task_context(
            task_type="GENERAL",
            goal="规划旅行",
            known_facts=(),
            missing_facts=(),
            available_tools=(),
        ),
        depends_on=depends_on,
    )


def _plan(*stages):
    return AdvisorTurnPlan(primary_task_type="GENERAL", stages=tuple(stages))


def test_stage_execution_marks_dependent_stage_blocked_after_failed_prerequisite():
    report = summarize_stage_execution(
        _plan(_stage("SEARCH_WEATHER"), _stage("SCHEDULE", ("SEARCH_WEATHER",))),
        {"SEARCH_WEATHER": {"status": "FAILED", "decision_source": "MODEL"}},
    )

    assert report["unexecuted_stages"] == ["SCHEDULE"]
    assert report["stage_statuses"][1]["status"] == "BLOCKED_DEPENDENCY"
    assert report["overall_status"] == "FAILED"


def test_stage_execution_resolves_dependencies_independent_of_plan_display_order():
    report = summarize_stage_execution(
        _plan(_stage("SCHEDULE", ("SEARCH_WEATHER",)), _stage("SEARCH_WEATHER")),
        {"SEARCH_WEATHER": {"status": "FAILED"}},
    )

    assert report["stage_statuses"][0]["status"] == "BLOCKED_DEPENDENCY"


def test_stage_execution_distinguishes_ready_but_omitted_stage_and_partial_result():
    report = summarize_stage_execution(
        _plan(_stage("SEARCH_WEATHER"), _stage("ROUTE")),
        {"SEARCH_WEATHER": {"status": "SUCCEEDED"}},
    )

    assert report["stage_statuses"][1]["status"] == "NOT_EXECUTED"
    assert report["overall_status"] == "PARTIAL"


def test_stage_execution_preserves_waiting_for_user_terminal_state():
    report = summarize_stage_execution(
        _plan(_stage("ROUTE")),
        {"ROUTE": {"status": "NEEDS_USER_INPUT", "decision_summary": "需要路线方式"}},
    )

    assert report["stage_statuses"][0]["status"] == "NEEDS_USER_INPUT"
    assert report["overall_status"] == "NEEDS_USER_INPUT"
    assert report["unexecuted_stages"] == []


def test_stage_queue_reorders_ready_stages_but_executes_all_authorized_stages():
    plan = _plan(_stage("SEARCH_WEATHER"), _stage("ROUTE"), _stage("SCHEDULE"))
    called = []

    async def run_route():
        called.append("ROUTE")
        return {"status": "SUCCEEDED"}

    async def run_schedule():
        called.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    result = asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"ROUTE", "SCHEDULE"},
        initial_statuses={"SEARCH_WEATHER": "SUCCEEDED"},
        preferred_order=["ROUTE", "SCHEDULE"],
        runners={"ROUTE": run_route, "SCHEDULE": run_schedule},
    ))

    assert called == ["ROUTE", "SCHEDULE"]
    assert result["statuses"]["ROUTE"] == "SUCCEEDED"
    assert result["statuses"]["SCHEDULE"] == "SUCCEEDED"
    assert result["remaining"] == []


def test_stage_queue_does_not_bypass_failed_dependencies():
    plan = _plan(_stage("SEARCH_WEATHER"), _stage("SCHEDULE", ("SEARCH_WEATHER",)))
    called = []

    async def run_schedule():
        called.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    result = asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"SCHEDULE"},
        initial_statuses={"SEARCH_WEATHER": "FAILED"},
        preferred_order=["SCHEDULE"],
        runners={"SCHEDULE": run_schedule},
    ))

    assert called == []
    assert result["remaining"] == ["SCHEDULE"]


def test_stage_queue_unlocks_required_dependent_after_live_prerequisite_succeeds():
    plan = _plan(_stage("SEARCH_WEATHER"), _stage("SCHEDULE", ("SEARCH_WEATHER",)))
    called = []

    async def run_search():
        called.append("SEARCH_WEATHER")
        return {"status": "SUCCEEDED", "evidence": "verified-candidates"}

    async def run_schedule():
        called.append("SCHEDULE")
        return {"status": "SUCCEEDED", "proposal": "draft"}

    result = asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"SEARCH_WEATHER", "SCHEDULE"},
        initial_statuses={},
        preferred_order=["SCHEDULE", "SEARCH_WEATHER"],
        runners={"SEARCH_WEATHER": run_search, "SCHEDULE": run_schedule},
    ))

    assert called == ["SEARCH_WEATHER", "SCHEDULE"]
    assert result["execution_order"] == ["SEARCH_WEATHER", "SCHEDULE"]
    assert result["remaining"] == []


def test_stage_queue_checkpoints_only_stage_names_and_terminal_statuses():
    plan = _plan(_stage("ROUTE"))
    events = []

    async def run_route():
        return {"status": "SUCCEEDED"}

    async def checkpoint(event):
        events.append(event)

    asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"ROUTE"},
        initial_statuses={},
        preferred_order=[],
        runners={"ROUTE": run_route},
        checkpoint=checkpoint,
    ))

    assert events == [
        {"state": "STAGE_STARTED", "transition": 1, "tool": "ROUTE"},
        {"state": "SUCCEEDED", "transition": 1, "tool": "ROUTE"},
    ]


def test_stage_queue_contains_unexpected_failure_and_continues_independent_work():
    plan = _plan(
        _stage("SEARCH_WEATHER"),
        _stage("ROUTE"),
        _stage("SCHEDULE", ("SEARCH_WEATHER",)),
    )
    called = []
    events = []

    async def fail_search():
        called.append("SEARCH_WEATHER")
        raise RuntimeError("provider secret and user query must not be persisted")

    async def run_route():
        called.append("ROUTE")
        return {"status": "SUCCEEDED"}

    async def run_schedule():
        called.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    async def checkpoint(event):
        events.append(event)

    result = asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"SEARCH_WEATHER", "ROUTE", "SCHEDULE"},
        initial_statuses={},
        preferred_order=["SEARCH_WEATHER", "SCHEDULE", "ROUTE"],
        runners={
            "SEARCH_WEATHER": fail_search,
            "ROUTE": run_route,
            "SCHEDULE": run_schedule,
        },
        checkpoint=checkpoint,
    ))

    assert called == ["SEARCH_WEATHER", "ROUTE"]
    assert result["statuses"]["SEARCH_WEATHER"] == "FAILED"
    assert result["statuses"]["ROUTE"] == "SUCCEEDED"
    assert result["remaining"] == ["SCHEDULE"]
    assert result["traces"]["SEARCH_WEATHER"]["failure_type"] == "RuntimeError"
    assert "provider secret" not in str(result)
    assert events[:2] == [
        {"state": "STAGE_STARTED", "transition": 1, "tool": "SEARCH_WEATHER"},
        {"state": "FAILED", "transition": 1, "tool": "SEARCH_WEATHER"},
    ]


def test_turn_executor_retains_terminal_state_and_checkpoint_sequence_across_phases():
    plan = _plan(
        _stage("ROUTE"),
        _stage("SEARCH_WEATHER"),
        _stage("SCHEDULE", ("SEARCH_WEATHER",)),
    )
    events = []
    calls = []

    async def checkpoint(event):
        events.append(event)

    async def run_route():
        calls.append("ROUTE")
        return {"status": "SUCCEEDED"}

    async def run_search():
        calls.append("SEARCH_WEATHER")
        return {"status": "SUCCEEDED"}

    async def run_schedule():
        calls.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    async def run_phases():
        executor = AdvisorTurnExecutor(plan, checkpoint=checkpoint)
        await executor.execute(
            requested_stages={"ROUTE"},
            preferred_order=["ROUTE"],
            runners={"ROUTE": run_route},
        )
        await executor.execute(
            requested_stages={"SEARCH_WEATHER", "SCHEDULE"},
            preferred_order=["SCHEDULE", "SEARCH_WEATHER"],
            runners={"SEARCH_WEATHER": run_search, "SCHEDULE": run_schedule},
        )
        return executor

    executor = asyncio.run(run_phases())

    assert calls == ["ROUTE", "SEARCH_WEATHER", "SCHEDULE"]
    assert executor.execution_order == calls
    assert executor.statuses == {
        "ROUTE": "SUCCEEDED",
        "SEARCH_WEATHER": "SUCCEEDED",
        "SCHEDULE": "SUCCEEDED",
    }
    assert [event["transition"] for event in events] == [1, 1, 2, 2, 3, 3]


def test_turn_executor_selection_cannot_add_omit_or_repeat_unplanned_stages():
    executor = AdvisorTurnExecutor(_plan(_stage("ROUTE"), _stage("SEARCH_WEATHER")))

    async def run_selection():
        return await executor.select_order(
            eligible_stages=["ROUTE", "SEARCH_WEATHER", "ROUTE", "SCHEDULE"],
            selector=lambda eligible: _chosen_order(
                eligible, ["SCHEDULE", "SEARCH_WEATHER"],
            ),
        )

    decision = asyncio.run(run_selection())

    assert decision["order"] == ["SEARCH_WEATHER", "ROUTE"]
    assert executor.selection_history == [{
        "eligible_stages": ["ROUTE", "SEARCH_WEATHER"],
        "order": ["SEARCH_WEATHER", "ROUTE"],
        "decision_source": "MODEL",
        "decision_summary": "选择了可执行阶段",
        "fallback_reason": None,
    }]


def test_turn_executor_checkpoints_only_stage_selection_contract():
    events = []

    async def checkpoint(event):
        events.append(event)

    executor = AdvisorTurnExecutor(
        _plan(_stage("ROUTE"), _stage("SEARCH_WEATHER")), checkpoint=checkpoint,
    )

    asyncio.run(executor.select_order(
        eligible_stages=["ROUTE", "SEARCH_WEATHER"],
        selector=lambda eligible: _chosen_order(eligible, ["SEARCH_WEATHER", "ROUTE"]),
    ))

    assert events == [{
        "state": "STAGE_SELECTED",
        "transition": 1,
        "tool": "SEARCH_WEATHER",
        "eligible_stages": ["ROUTE", "SEARCH_WEATHER"],
        "selected_order": ["SEARCH_WEATHER", "ROUTE"],
        "decision_source": "MODEL",
    }]


def test_turn_executor_displays_recovered_selection_as_historical_only():
    executor = AdvisorTurnExecutor(
        _plan(_stage("ROUTE"), _stage("SEARCH_WEATHER")),
        recovery={
            "has_previous_attempt": True,
            "selection_history": [{
                "eligible_stages": ["ROUTE", "SEARCH_WEATHER"],
                "order": ["ROUTE", "SEARCH_WEATHER"],
                "decision_source": "MODEL",
            }],
        },
    )

    assert executor.selection_history == [{
        "eligible_stages": ["ROUTE", "SEARCH_WEATHER"],
        "order": ["ROUTE", "SEARCH_WEATHER"],
        "decision_source": "RECOVERED",
        "original_decision_source": "MODEL",
        "decision_summary": "上次尝试的阶段顺序仅供参考，本次会重新核验并决策",
        "fallback_reason": None,
    }]


def test_turn_executor_falls_back_to_all_authorized_stages_when_selector_crashes():
    executor = AdvisorTurnExecutor(_plan(_stage("ROUTE"), _stage("SEARCH_WEATHER")))
    called = []

    async def broken_selector(_eligible):
        raise RuntimeError("private provider response")

    async def run_route():
        called.append("ROUTE")
        return {"status": "SUCCEEDED"}

    async def run_search():
        called.append("SEARCH_WEATHER")
        return {"status": "SUCCEEDED"}

    async def run_turn():
        decision = await executor.select_order(
            eligible_stages=["ROUTE", "SEARCH_WEATHER"],
            selector=broken_selector,
        )
        result = await executor.execute(
            requested_stages={"ROUTE", "SEARCH_WEATHER"},
            preferred_order=decision["order"],
            runners={"ROUTE": run_route, "SEARCH_WEATHER": run_search},
        )
        return decision, result

    decision, result = asyncio.run(run_turn())

    assert decision["order"] == ["ROUTE", "SEARCH_WEATHER"]
    assert decision["decision_source"] == "RUNTIME_GUARD"
    assert decision["fallback_reason"] == "SELECTOR_ERROR:RuntimeError"
    assert "private provider response" not in str(decision)
    assert called == ["ROUTE", "SEARCH_WEATHER"]
    assert result["remaining"] == []


@pytest.mark.parametrize(
    ("rain_observed", "expected_order"),
    [
        (True, ["SEARCH_WEATHER", "ROUTE", "SCHEDULE"]),
        (False, ["SEARCH_WEATHER", "SCHEDULE", "ROUTE"]),
    ],
)
def test_adaptive_executor_reselects_after_observing_completed_stage(
    rain_observed, expected_order,
):
    plan = _plan(_stage("SEARCH_WEATHER"), _stage("ROUTE"), _stage("SCHEDULE"))
    executor = AdvisorTurnExecutor(plan)
    calls = []
    observed_states = []

    async def choose(eligible, statuses, traces):
        observed_states.append((list(eligible), dict(statuses)))
        if not statuses:
            selected = "SEARCH_WEATHER"
        elif traces["SEARCH_WEATHER"]["rain"]:
            selected = "ROUTE"
        else:
            selected = "SCHEDULE"
        return {
            "order": [selected],
            "decision_source": "MODEL",
            "decision_summary": f"observed weather rain={traces.get('SEARCH_WEATHER', {}).get('rain')}",
        }

    async def search_weather():
        calls.append("SEARCH_WEATHER")
        return {"status": "SUCCEEDED", "rain": rain_observed}

    async def route():
        calls.append("ROUTE")
        return {"status": "SUCCEEDED"}

    async def schedule():
        calls.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    result = asyncio.run(executor.execute_adaptive(
        requested_stages={"SEARCH_WEATHER", "ROUTE", "SCHEDULE"},
        runners={
            "SEARCH_WEATHER": search_weather,
            "ROUTE": route,
            "SCHEDULE": schedule,
        },
        selector=choose,
    ))

    assert calls == expected_order
    assert result["execution_order"] == expected_order
    assert result["remaining"] == []
    assert len(observed_states) == 2
    assert observed_states[1][1] == {"SEARCH_WEATHER": "SUCCEEDED"}
    assert executor.selection_history[0]["order"] == ["SEARCH_WEATHER", "ROUTE", "SCHEDULE"]
    assert executor.selection_history[1]["order"][0] == expected_order[1]


def test_adaptive_executor_refreshes_domain_observation_before_next_decision():
    plan = _plan(
        _stage("SEARCH_WEATHER"),
        _stage("ROUTE", depends_on=("SEARCH_WEATHER",)),
        _stage("SCHEDULE", depends_on=("SEARCH_WEATHER",)),
    )
    executor = AdvisorTurnExecutor(plan)
    observations = []

    async def search_weather():
        return {"status": "SUCCEEDED", "candidate_count": 3}

    async def schedule():
        return {"status": "SUCCEEDED"}

    async def route():
        return {"status": "SUCCEEDED"}

    async def refresh_domain_state(stage, trace):
        if stage == "SEARCH_WEATHER":
            trace["verified_candidate_count"] = trace.pop("candidate_count")

    async def choose(eligible, statuses, traces):
        observations.append((eligible, statuses, traces))
        return {"order": eligible, "decision_source": "MODEL"}

    result = asyncio.run(executor.execute_adaptive(
        requested_stages={"SEARCH_WEATHER", "SCHEDULE", "ROUTE"},
        runners={
            "SEARCH_WEATHER": search_weather, "SCHEDULE": schedule, "ROUTE": route,
        },
        selector=choose,
        after_stage=refresh_domain_state,
    ))

    assert observations[0][2]["SEARCH_WEATHER"]["verified_candidate_count"] == 3
    assert result["traces"]["SEARCH_WEATHER"]["verified_candidate_count"] == 3
    assert result["execution_order"][0] == "SEARCH_WEATHER"
    assert set(result["execution_order"]) == {"SEARCH_WEATHER", "ROUTE", "SCHEDULE"}


def test_adaptive_executor_blocks_dependencies_if_stage_observation_refresh_fails():
    plan = _plan(_stage("SEARCH_WEATHER"), _stage("SCHEDULE", depends_on=("SEARCH_WEATHER",)))
    executor = AdvisorTurnExecutor(plan)
    calls = []

    async def search_weather():
        calls.append("SEARCH_WEATHER")
        return {"status": "SUCCEEDED"}

    async def schedule():
        calls.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    async def failed_refresh(_stage, _trace):
        raise RuntimeError("private merge detail")

    async def choose(eligible, _statuses, _traces):
        return {"order": eligible, "decision_source": "MODEL"}

    result = asyncio.run(executor.execute_adaptive(
        requested_stages={"SEARCH_WEATHER", "SCHEDULE"},
        runners={"SEARCH_WEATHER": search_weather, "SCHEDULE": schedule},
        selector=choose,
        after_stage=failed_refresh,
    ))

    assert calls == ["SEARCH_WEATHER"]
    assert result["statuses"]["SEARCH_WEATHER"] == "FAILED"
    assert result["remaining"] == ["SCHEDULE"]
    assert "private merge detail" not in str(result)


def test_turn_executor_does_not_call_selector_when_no_stage_is_eligible():
    executor = AdvisorTurnExecutor(_plan(_stage("ROUTE")))

    async def must_not_run(_eligible):
        raise AssertionError("empty selection must not call model policy")

    decision = asyncio.run(executor.select_order(
        eligible_stages=["UNKNOWN"], selector=must_not_run,
    ))

    assert decision["order"] == []
    assert decision["decision_source"] == "RULE"


async def _chosen_order(eligible, proposed):
    return {
        "order": proposed,
        "decision_source": "MODEL",
        "decision_summary": "选择了可执行阶段",
        "fallback_reason": None,
    }


def test_turn_executor_continues_persisted_sequence_and_exposes_safe_recovery_state():
    events = []

    async def checkpoint(event):
        events.append(event)

    async def run_route():
        return {"status": "SUCCEEDED"}

    async def run_turn():
        executor = AdvisorTurnExecutor(
            _plan(_stage("ROUTE")),
            checkpoint=checkpoint,
            initial_transition=2,
            recovery={
                "has_previous_attempt": True,
                "interrupted_stage": "ROUTE",
            },
        )
        await executor.execute(
            requested_stages={"ROUTE"},
            preferred_order=["ROUTE"],
            runners={"ROUTE": run_route},
        )
        return executor

    executor = asyncio.run(run_turn())

    assert [event["transition"] for event in events] == [3, 3]
    assert executor.recovery == {
        "resumed_same_task": True,
        "interrupted_stage": "ROUTE",
        "completed_stages": [],
    }


@pytest.mark.parametrize("invalid_outcome", [None, {}, {"status": "UNKNOWN"}, {"status": []}])
def test_invalid_stage_outcome_is_failure_and_blocks_dependent_stage(invalid_outcome):
    plan = _plan(
        _stage("SEARCH_WEATHER"),
        _stage("SCHEDULE", ("SEARCH_WEATHER",)),
        _stage("ROUTE"),
    )
    calls = []
    events = []

    async def checkpoint(event):
        events.append(event)

    async def invalid_search():
        calls.append("SEARCH_WEATHER")
        return invalid_outcome

    async def schedule():
        calls.append("SCHEDULE")
        return {"status": "SUCCEEDED"}

    async def route():
        calls.append("ROUTE")
        return {"status": "SUCCEEDED"}

    result = asyncio.run(execute_ready_stage_queue(
        plan,
        requested_stages={"SEARCH_WEATHER", "SCHEDULE", "ROUTE"},
        initial_statuses={},
        preferred_order=["SEARCH_WEATHER", "SCHEDULE", "ROUTE"],
        runners={
            "SEARCH_WEATHER": invalid_search,
            "SCHEDULE": schedule,
            "ROUTE": route,
        },
        checkpoint=checkpoint,
    ))

    assert calls == ["SEARCH_WEATHER", "ROUTE"]
    assert result["statuses"] == {"SEARCH_WEATHER": "FAILED", "ROUTE": "SUCCEEDED"}
    assert result["traces"]["SEARCH_WEATHER"]["error_code"] == "INVALID_STAGE_OUTCOME"
    assert result["remaining"] == ["SCHEDULE"]
    assert events[1]["state"] == "FAILED"
