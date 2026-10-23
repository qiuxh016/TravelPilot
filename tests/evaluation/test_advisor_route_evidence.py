from datetime import UTC, datetime, timedelta

import pytest
import travelpilot_api.advisor.route_evidence as route_agent
from pydantic import BaseModel
from travelpilot_api.advisor.route_evidence import gather_route_evidence, reusable_route_evidence
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.agents.planner import TokenUsage
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure, ToolSpec


class MatrixInput(BaseModel):
    points: list[tuple[float, float]]


async def _trace(_entry):
    return None


def _runtime(handler):
    return AgentRuntime(
        [ToolSpec("get_distance_matrix", MatrixInput, handler, retry_limit=0)],
        _trace,
        max_steps=1,
    )


@pytest.mark.asyncio
async def test_route_evidence_uses_server_points_and_verifies_matrix():
    seen = []
    persisted = []

    async def matrix(arguments):
        seen.extend(arguments.points)
        return {
            "durations_min": [[0, 12], [14, 0]],
            "status": "SIMULATED",
            "source": "test fixture",
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    points = [(30.1, 104.1), (30.2, 104.2)]
    result = await gather_route_evidence(
        _runtime(matrix), points,
        trip_context={"has_dates": True, "fixed_stop_count": 1},
        api_key="", model="", base_url="", token_budget=128,
        route_observation_callback=lambda value: _append(persisted, value),
    )

    assert seen == points
    assert result.matrix == [[0, 12], [14, 0]]
    assert result.evidence_status == "SIMULATED"
    assert result.evidence_source == "test fixture"
    assert persisted == [{
        "durations_min": [[0, 12], [14, 0]],
        "status": "SIMULATED", "source": "test fixture",
        "retrieved_at": persisted[0]["retrieved_at"],
    }]
    assert result.loop.status == "SUCCEEDED"


async def _append(target, value):
    target.append(value)


@pytest.mark.asyncio
async def test_route_evidence_stops_after_provider_failure_and_exposes_observation():
    calls = 0

    async def matrix(_arguments):
        nonlocal calls
        calls += 1
        raise ToolFailure("MAP_UNAVAILABLE")

    result = await gather_route_evidence(
        _runtime(matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="", model="", base_url="", token_budget=128,
    )

    assert result.matrix is None
    assert result.evidence_status == "UNAVAILABLE"
    assert result.loop.status == "SUCCEEDED"
    assert result.loop.observation["get_distance_matrix_failure"]["error"] == "MAP_UNAVAILABLE"
    assert calls == 1


@pytest.mark.asyncio
async def test_route_evidence_reuses_valid_observation_without_tool_call():
    async def unexpected(_arguments):
        raise AssertionError("fresh route evidence should avoid another map request")

    cached = {
        "durations_min": [[0, 8], [9, 0]],
        "status": "VERIFIED",
        "source": "AMap Web Service",
        "retrieved_at": "2026-10-18T08:00:00+00:00",
    }
    result = await gather_route_evidence(
        _runtime(unexpected), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="", model="", base_url="", token_budget=128,
        cached_evidence=cached,
    )

    assert result.matrix == cached["durations_min"]
    assert result.evidence_status == "VERIFIED"
    assert result.loop.transitions == []
    assert result.decision_source == "OBSERVATION"


@pytest.mark.asyncio
async def test_recovered_route_evidence_resumes_at_schedule_builder_without_reselecting(monkeypatch):
    received_matrices = []

    async def unexpected_matrix(_arguments):
        raise AssertionError("a verified matrix snapshot should skip the provider")

    async def build(matrix):
        received_matrices.append(matrix)
        return {
            "feasible": True,
            "candidate_ids": ["place-a", "place-b"],
            "schedules": [
                {"candidate_id": "place-a", "day": "2026-11-02", "start_time": "09:00"},
                {"candidate_id": "place-b", "day": "2026-11-02", "start_time": "11:00"},
            ],
            "warnings": [],
        }

    async def unexpected_model_decision(*_args, **_kwargs):
        raise AssertionError("recovery should continue from the verified observation")

    monkeypatch.setattr(
        "travelpilot_api.advisor.route_evidence.decide_next_action", unexpected_model_decision,
    )
    cached = {
        "durations_min": [[0, 8], [9, 0]],
        "status": "VERIFIED", "source": "AMap Web Service",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    result = await gather_route_evidence(
        _runtime(unexpected_matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="configured", model="model", base_url="http://local.invalid/v1",
        token_budget=128, cached_evidence=cached, schedule_builder=build,
    )

    assert result.loop.status == "SUCCEEDED"
    assert result.draft["feasible"] is True
    assert received_matrices == [cached["durations_min"]]
    assert result.decision_source == "RECOVERED"
    assert [item["tool"] for item in result.loop.transitions] == ["build_schedule_draft"]


@pytest.mark.asyncio
async def test_existing_trip_agent_runs_route_then_nonpersistent_plan_and_finishes():
    observed_matrix = []
    expected_ids = ["place-a", "place-b"]

    async def matrix(_arguments):
        return {"durations_min": [[0, 12], [14, 0]], "status": "VERIFIED", "source": "test"}

    async def build(route_matrix):
        observed_matrix.append(route_matrix)
        return {
            "feasible": True,
            "candidate_ids": expected_ids,
            "schedules": [
                {"candidate_id": "place-a", "day": "2026-11-02", "start_time": "09:00"},
                {"candidate_id": "place-b", "day": "2026-11-02", "start_time": "11:00"},
            ],
            "warnings": [],
        }

    result = await gather_route_evidence(
        _runtime(matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="", model="", base_url="", token_budget=128,
        schedule_builder=build,
    )

    assert result.loop.status == "SUCCEEDED"
    assert [step["tool"] for step in result.loop.transitions] == [
        "get_distance_matrix", "build_schedule_draft",
    ]
    assert observed_matrix == [[[0, 12], [14, 0]]]
    assert result.draft["feasible"] is True


@pytest.mark.asyncio
async def test_runtime_requires_route_evidence_before_multi_stop_draft(monkeypatch):
    choices = []

    async def matrix(_arguments):
        return {"durations_min": [[0, 12], [14, 0]], "status": "VERIFIED", "source": "test"}

    async def fake_decide(observation, schemas, **_kwargs):
        choices.append((observation, set(schemas)))
        return AgentAction(
            kind="CALL_TOOL", tool="build_schedule_draft", summary="路程较短，先排草案",
        ), TokenUsage()

    async def build(matrix):
        assert matrix == [[0, 12], [14, 0]]
        return {
            "feasible": True,
            "candidate_ids": ["place-a", "place-b"],
            "schedules": [{"candidate_id": "place-a"}, {"candidate_id": "place-b"}],
            "warnings": [],
        }

    monkeypatch.setattr(route_agent, "decide_next_action", fake_decide)
    result = await gather_route_evidence(
        _runtime(matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        user_goal="帮我安排轻松的两日行程，第二天下午保留预约",
        api_key="synthetic-key", model="synthetic-policy", base_url="http://unused",
        token_budget=128, schedule_builder=build,
    )

    assert result.loop.status == "SUCCEEDED"
    assert [transition["tool"] for transition in result.loop.transitions] == [
        "get_distance_matrix", "build_schedule_draft",
    ]
    assert "build_schedule_draft" not in choices[0][1]
    assert "get_distance_matrix" in choices[0][1]


@pytest.mark.asyncio
async def test_model_observes_route_result_before_choosing_the_planner_action(monkeypatch):
    choices = []
    actions = iter([
        AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", summary="请求生成草案"),
        AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", summary="路线已足够，生成草案"),
    ])

    async def matrix(_arguments):
        return {"durations_min": [[0, 12], [14, 0]], "status": "VERIFIED", "source": "test"}

    async def fake_decide(observation, schemas, **_kwargs):
        choices.append((observation, set(schemas)))
        return next(actions), TokenUsage()

    async def build(route_matrix):
        assert route_matrix == [[0, 12], [14, 0]]
        return {
            "feasible": True,
            "candidate_ids": ["place-a", "place-b"],
            "schedules": [{"candidate_id": "place-a"}, {"candidate_id": "place-b"}],
            "warnings": [],
        }

    monkeypatch.setattr(route_agent, "decide_next_action", fake_decide)
    result = await gather_route_evidence(
        _runtime(matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"destination": "成都", "start_date": "2026-11-02", "end_date": "2026-11-03",
                      "pace_level": 2, "has_dates": True, "fixed_stop_count": 1},
        user_goal="帮我安排轻松的两日行程，第二天下午保留预约",
        api_key="synthetic-key", model="synthetic-policy", base_url="http://unused",
        token_budget=256, schedule_builder=build,
    )

    assert result.loop.status == "SUCCEEDED"
    assert len(choices) == 2
    assert "get_distance_matrix" in choices[0][1]
    assert "build_schedule_draft" not in choices[0][1]
    assert choices[0][0]["user_goal"] == "帮我安排轻松的两日行程，第二天下午保留预约"
    assert choices[0][0]["trip_context"] == {
        "destination": "成都", "start_date": "2026-11-02", "end_date": "2026-11-03",
        "pace_level": 2, "stop_count": 2, "has_dates": True,
        "fixed_stop_count": 1, "route_evidence_needed_for_multi_stop_schedule": True,
    }
    assert "get_distance_matrix" not in choices[1][1]
    assert "get_distance_matrix" in choices[1][0]
    assert choices[1][1] == {"build_schedule_draft"}


@pytest.mark.asyncio
async def test_agent_can_pause_and_ask_for_missing_preference(monkeypatch):
    async def decide(_observation, _schemas, **_kwargs):
        return AgentAction(
            kind="ASK_USER", question="你希望每天最多安排几个地点？",
        ), TokenUsage()

    async def build(_matrix):
        raise AssertionError("the Agent should wait for the user's answer")

    monkeypatch.setattr(route_agent, "decide_next_action", decide)
    result = await gather_route_evidence(
        _runtime(lambda _arguments: None), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="synthetic-key", model="synthetic-policy", base_url="http://unused",
        token_budget=128, schedule_builder=build,
    )

    assert result.loop.status == "NEEDS_USER_INPUT"
    assert result.question == "你希望每天最多安排几个地点？"
    assert result.draft is None
    assert result.loop.transitions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["provider", "invalid_matrix"])
async def test_existing_trip_agent_recovers_to_safe_local_plan(failure_mode):
    received_matrices = []
    persisted_observations = []

    async def matrix(_arguments):
        if failure_mode == "provider":
            raise ToolFailure("MAP_UNAVAILABLE")
        return {"durations_min": [[0, -1], [3]], "status": "VERIFIED", "source": "bad"}

    async def build(route_matrix):
        received_matrices.append(route_matrix)
        return {
            "feasible": True,
            "candidate_ids": ["place-a", "place-b"],
            "schedules": [
                {"candidate_id": "place-a", "day": "2026-11-02", "start_time": "09:00"},
                {"candidate_id": "place-b", "day": "2026-11-02", "start_time": "11:00"},
            ],
            "warnings": ["路线证据不可用，使用本地估算"],
        }

    result = await gather_route_evidence(
        _runtime(matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="", model="", base_url="", token_budget=128,
        schedule_builder=build,
        route_observation_callback=lambda value: _append(persisted_observations, value),
    )

    assert result.loop.status == "SUCCEEDED"
    assert result.draft["feasible"] is True
    assert received_matrices == [None]
    assert persisted_observations == []
    if failure_mode == "provider":
        assert result.evidence_status == "UNAVAILABLE"
    else:
        assert result.evidence_status == "REJECTED"
        assert any(step["status"] == "REJECTED" for step in result.loop.transitions)


@pytest.mark.asyncio
async def test_rejected_schedule_draft_does_not_mark_valid_route_evidence_rejected():
    async def unexpected_matrix(_arguments):
        raise AssertionError("the fresh cached route matrix should be reused")

    async def build(_route_matrix):
        return {"feasible": True, "candidate_ids": ["place-a", "place-b"], "schedules": []}

    cached = {
        "durations_min": [[0, 8], [9, 0]],
        "status": "VERIFIED",
        "source": "test map",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    result = await gather_route_evidence(
        _runtime(unexpected_matrix), [(30.1, 104.1), (30.2, 104.2)],
        trip_context={"has_dates": True, "fixed_stop_count": 0},
        api_key="", model="", base_url="", token_budget=128,
        cached_evidence=cached, schedule_builder=build,
    )

    assert result.evidence_status == "VERIFIED"
    assert result.matrix == cached["durations_min"]
    assert result.draft is None
    assert result.loop.observation.get("build_schedule_draft_verification_failure")


def test_route_cache_requires_fresh_matching_context_and_reorders_matrix():
    now = datetime.now(UTC)
    cache = {
        "candidate_ids": ["a", "b"],
        "context_revision": 7,
        "durations_min": [[0, 11], [13, 0]],
        "status": "VERIFIED",
        "source": "AMap Web Service",
        "retrieved_at": (now - timedelta(minutes=4)).isoformat(),
    }

    reused = reusable_route_evidence(cache, ["b", "a"], 7, now=now)

    assert reused == {
        "durations_min": [[0, 13], [11, 0]],
        "status": "VERIFIED",
        "source": "AMap Web Service",
        "retrieved_at": cache["retrieved_at"],
    }
    assert reusable_route_evidence(cache, ["a", "b"], 8, now=now) is None
    assert reusable_route_evidence(
        cache, ["a", "b"], 7, expected_source="Other map provider", now=now,
    ) is None
    assert reusable_route_evidence(
        {**cache, "retrieved_at": (now - timedelta(minutes=11)).isoformat()},
        ["a", "b"], 7, now=now,
    ) is None
    assert reusable_route_evidence(
        {**cache, "status": "UNAVAILABLE"}, ["a", "b"], 7, now=now,
    ) is None
    assert reusable_route_evidence(
        {**cache, "retrieved_at": now.replace(tzinfo=None).isoformat()},
        ["a", "b"], 7, now=now,
    ) is None
    assert reusable_route_evidence(
        {**cache, "status": "SIMULATED"}, ["a", "b"], 7, now=now,
    ) is None
    assert reusable_route_evidence(
        {key: value for key, value in cache.items() if key != "retrieved_at"},
        ["a", "b"], 7, now=now,
    ) is None
    assert reusable_route_evidence(
        {**cache, "durations_min": [[5, 11], [13, 0]]},
        ["a", "b"], 7, now=now,
    ) is None
