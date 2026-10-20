import pytest
from travelpilot_api.advisor.stage_execution import AdvisorTurnExecutor, summarize_stage_execution
from travelpilot_api.advisor.turn_orchestration import choose_stage_order
from travelpilot_api.advisor.turn_plan import build_advisor_turn_plan
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.agents.planner import PlannerDecisionFailure, TokenUsage
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure


async def _trace(_entry):
    return None


def _runtime(budget=128):
    return AgentRuntime([], _trace, token_budget=budget)


@pytest.mark.asyncio
async def test_model_can_choose_route_before_schedule_without_dropping_either_stage(monkeypatch):
    async def choose(_observation, schemas, **kwargs):
        assert set(schemas) == {"run_schedule_stage", "run_route_stage"}
        assert kwargs["token_budget"] <= 64
        return AgentAction(
            kind="CALL_TOOL", tool="run_route_stage", arguments={},
            summary="先核验用户明确询问的路线，再整理行程",
        ), TokenUsage(prompt_tokens=31, completion_tokens=12, total_tokens=43)

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE", "ROUTE"],
        observation={"user_goal": "安排时间并查询路线"},
        runtime=runtime,
        api_key="test-key",
        model="test-model",
        base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["ROUTE", "SCHEDULE"]
    assert result["decision_source"] == "MODEL"
    assert runtime.output_tokens_used == 12
    assert runtime.input_tokens_used == 31


@pytest.mark.asyncio
async def test_model_can_choose_route_before_weather_without_dropping_either_stage(monkeypatch):
    async def choose(_observation, schemas, **_kwargs):
        assert set(schemas) == {"run_search_weather_stage", "run_route_stage"}
        return AgentAction(
            kind="CALL_TOOL", tool="run_route_stage", arguments={},
            summary="先确认已保存地点之间的路线",
        ), TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28)

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    result = await choose_stage_order(
        stages=["SEARCH_WEATHER", "ROUTE"], observation={}, runtime=_runtime(),
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["ROUTE", "SEARCH_WEATHER"]
    assert result["decision_source"] == "MODEL"


@pytest.mark.asyncio
async def test_invalid_selector_action_uses_fixed_order_and_keeps_usage(monkeypatch):
    async def choose(_observation, _schemas, **_kwargs):
        return AgentAction(kind="FINISH", summary="done"), TokenUsage(
            prompt_tokens=20, completion_tokens=9, total_tokens=29,
        )

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE", "ROUTE"], observation={}, runtime=runtime,
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["SCHEDULE", "ROUTE"]
    assert result["decision_source"] == "RULE_RECOVERY"
    assert result["fallback_reason"] == "INVALID_STAGE_CHOICE"
    assert runtime.output_tokens_used == 9


@pytest.mark.asyncio
async def test_selector_provider_failure_falls_back_without_losing_required_stages(monkeypatch):
    async def choose(_observation, _schemas, **_kwargs):
        raise PlannerDecisionFailure("PLANNER_TIMEOUT", TokenUsage(17, 4, 21))

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE", "ROUTE"], observation={}, runtime=runtime,
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["SCHEDULE", "ROUTE"]
    assert result["decision_source"] == "RULE_RECOVERY"
    assert result["fallback_reason"] == "PLANNER_TIMEOUT"
    assert runtime.total_tokens_used == 21


@pytest.mark.asyncio
async def test_unexpected_selector_error_releases_reserved_tokens_and_falls_back(monkeypatch):
    async def choose(_observation, _schemas, **_kwargs):
        raise RuntimeError("private provider response")

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE", "ROUTE"], observation={}, runtime=runtime,
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["SCHEDULE", "ROUTE"]
    assert result["decision_source"] == "RULE_RECOVERY"
    assert result["fallback_reason"] == "STAGE_SELECTOR_INTERNAL_ERROR"
    assert "private provider response" not in str(result)
    assert runtime.output_tokens_used == 0
    assert runtime.reserve_output_tokens(128) == 128


@pytest.mark.asyncio
async def test_selector_over_budget_usage_is_recorded_but_decision_is_rejected(monkeypatch):
    async def choose(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="CALL_TOOL", tool="run_route_stage", arguments={}, summary="route",
        ), TokenUsage(prompt_tokens=5, completion_tokens=129, total_tokens=134)

    monkeypatch.setattr(
        "travelpilot_api.advisor.turn_orchestration.decide_next_action", choose,
    )
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE", "ROUTE"], observation={}, runtime=runtime,
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["decision_source"] == "RULE_RECOVERY"
    assert result["fallback_reason"] == "TOKEN_BUDGET_EXCEEDED"
    assert result["order"] == ["SCHEDULE", "ROUTE"]
    assert runtime.output_tokens_used == 129
    with pytest.raises(ToolFailure, match="TOKEN_BUDGET_EXCEEDED"):
        runtime.reserve_output_tokens(1)


@pytest.mark.asyncio
async def test_one_stage_needs_no_extra_model_decision():
    runtime = _runtime()
    result = await choose_stage_order(
        stages=["SCHEDULE"], observation={}, runtime=runtime,
        api_key="test-key", model="test-model", base_url="http://local.invalid/v1",
        token_budget=128,
    )

    assert result["order"] == ["SCHEDULE"]
    assert result["decision_source"] == "RULE"
    assert runtime.total_tokens_used == 0


@pytest.mark.asyncio
async def test_cross_domain_selector_only_sees_ready_stages_and_keeps_every_required_stage(monkeypatch):
    plan = build_advisor_turn_plan(
        goal="找几个景点，再规划时间并查询景点之间的路线",
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
    assert [stage.name for stage in plan.ready_stages()] == ["SEARCH_WEATHER"]
    ready = plan.ready_stages({"SEARCH_WEATHER"})
    assert [stage.name for stage in ready] == ["SCHEDULE", "ROUTE"]
    stage_contracts = {
        stage.name: {
            "task_type": stage.task_context.task_type,
            "allowed_tools": stage.task_context.allowed_tools,
            "approval_boundary": stage.task_context.approval_boundary,
            "depends_on": list(stage.depends_on),
        }
        for stage in plan.stages
    }

    async def choose(observation, schemas, **_kwargs):
        assert observation["eligible_stages"] == ["SCHEDULE", "ROUTE"]
        assert observation["stage_contracts"]["ROUTE"]["depends_on"] == ["SEARCH_WEATHER"]
        assert set(schemas) == {"run_schedule_stage", "run_route_stage"}
        return AgentAction(
            kind="CALL_TOOL", tool="run_route_stage", arguments={},
            summary="搜索证据已就绪，先回答用户明确询问的路线",
        ), TokenUsage(prompt_tokens=18, completion_tokens=7, total_tokens=25)

    monkeypatch.setattr("travelpilot_api.advisor.turn_orchestration.decide_next_action", choose)
    result = await choose_stage_order(
        stages=[stage.name for stage in ready],
        observation={
            "eligible_stages": [stage.name for stage in ready],
            "stage_contracts": stage_contracts,
            "completed_stage_observations": {"search_weather": {"candidate_count": 3}},
        },
        runtime=_runtime(), api_key="test-key", model="test-model",
        base_url="http://local.invalid/v1", token_budget=128,
    )

    assert result["order"] == ["ROUTE", "SCHEDULE"]
    assert result["decision_source"] == "MODEL"


@pytest.mark.asyncio
async def test_route_without_selected_trip_is_reported_as_waiting_stage():
    plan = build_advisor_turn_plan(
        goal="人民公园到武侯祠怎么走",
        planning_category=None,
        trip_available=False,
        destination_available=False,
        dates_available=False,
        schedule_requested=False,
        weather_requested=False,
        preparation_requested=False,
        discovery_requested=False,
        search_requested=False,
        route_requested=True,
        available_search_tools=[],
        missing_facts=["destination", "trip_dates"],
    )
    route = plan.stage("ROUTE")
    assert route is not None and route.task_context.allowed_tools == []
    executor = AdvisorTurnExecutor(plan)
    called = False

    async def request_trip_selection():
        nonlocal called
        called = True
        return {
            "status": "NEEDS_USER_INPUT",
            "decision_source": "RULE",
            "decision_summary": "请先选择旅行",
        }

    execution = await executor.execute(
        requested_stages={"ROUTE"},
        preferred_order=["ROUTE"],
        runners={"ROUTE": request_trip_selection},
    )
    report = summarize_stage_execution(plan, execution["traces"])

    assert called
    assert execution["statuses"] == {"ROUTE": "NEEDS_USER_INPUT"}
    assert report["overall_status"] == "NEEDS_USER_INPUT"
    assert report["stage_statuses"][0]["stage"] == "ROUTE"
