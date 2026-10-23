from types import SimpleNamespace

import pytest
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.agents.planner import PlannerDecisionFailure, TokenUsage

from tests.evaluation import advisor_unseen_multiturn_eval as evaluation


@pytest.mark.asyncio
async def test_live_pilot_enforces_request_and_output_caps_without_answer_leak(monkeypatch):
    calls = []

    async def fake_decision(observation, schemas, **kwargs):
        calls.append((observation, schemas, kwargs["token_budget"]))
        return AgentAction(kind="ASK_USER", question="请补充一个必要信息"), SimpleNamespace(
            prompt_tokens=25,
            completion_tokens=8,
            total_tokens=33,
        )

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    report = await evaluation.run_live(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        request_cap=3,
        output_token_cap=128,
        total_output_token_cap=384,
    )

    assert report["requests_used"] == 3
    assert report["output_tokens_reported"] == 24
    assert report["aggregate_requested_output_ceiling"] == 384
    assert len(calls) == 3
    assert all(cap == 128 for _, _, cap in calls)
    assert all("expected_action" not in observation for observation, _, _ in calls)
    assert all(observation["tool_observations_are_local_fixtures"] for observation, _, _ in calls)
    assert all(
        row["formal_write"] is False
        for rows in report["groups"].values()
        for case in rows
        for row in case["turns"]
    )


@pytest.mark.asyncio
async def test_live_pilot_rejects_a_budget_above_approved_request_ceiling():
    with pytest.raises(ValueError, match="request cap"):
        await evaluation.run_live(
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
            request_cap=evaluation.DEFAULT_REQUEST_CAP + 1,
        )


def test_route_matrix_verifier_rejects_malformed_fixture():
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    case = next(
        row for row in dataset["cases"] if row["id"] == "v3-schedule-protect-appointment-02"
    )
    malformed_case = {
        **case,
        "initial_state": {**case["initial_state"], "route_matrix": "malformed"},
    }
    result = {
        "status": "VERIFIED_FIXTURE",
        "source": "LOCAL_FIXTURE",
        "durations_min": [[0, -1], [4, 0]],
    }

    assert not evaluation._verified_fixture("get_distance_matrix", result, malformed_case)


def test_b3_schedule_draft_fixture_matches_verifier_contract():
    case = {
        "initial_state": {
            "saved_place_details": [{"id": "place-a"}, {"id": "place-b"}],
        },
    }

    result = evaluation.action_eval._fixture_observation("build_schedule_draft", case)

    assert result["status"] == "DRAFT_FIXTURE"
    assert result["formal_write"] is False
    assert evaluation._verified_fixture("build_schedule_draft", result, case)


@pytest.mark.asyncio
async def test_b3_recovery_does_not_repeat_a_failed_provider_action(monkeypatch):
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    source_case = next(
        row for row in dataset["cases"] if row["id"] == "v3-schedule-protect-appointment-02"
    )
    case = {
        **source_case,
        "initial_state": {**source_case["initial_state"], "route_provider": "timeout"},
    }
    turn = case["turns"][0]
    seen = []

    async def fake_decision(observation, schemas, **kwargs):
        seen.append(schemas)
        if len(seen) == 1:
            points = [
                (place["latitude"], place["longitude"])
                for place in case["initial_state"]["saved_place_details"]
            ]
            action = AgentAction(
                kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": points}
            )
        else:
            action = AgentAction(kind="ASK_USER", question="地图服务失败，要稍后重试吗？")
        return action, SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    monkeypatch.setattr(evaluation, "decide_next_action", fake_decision)
    row = await evaluation._run_turn(
        case=case,
        turn=turn,
        turn_index=0,
        arm="B3_VERIFIER_RECOVERY",
        history=[{"role": "user", "content": turn["user"]}],
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        output_token_cap=128,
        global_budget={
            "requests": 0,
            "output_tokens": 0,
            "request_cap": 10,
            "exhausted": 0,
            "reported_overrun": 0,
        },
    )

    assert "get_distance_matrix" in seen[0]
    assert "get_distance_matrix" not in seen[1]
    assert row["loop_status"] == "NEEDS_USER_INPUT"
    assert row["tool_transitions"] == [
        {
            "tool": "get_distance_matrix",
            "status": "FAILED",
            "error": "MAP_TIMEOUT",
            "latency_ms": 0,
        }
    ]


def test_live_cap_defaults_fit_user_approved_ceiling():
    assert evaluation.DEFAULT_REQUEST_CAP == 120
    assert evaluation.DEFAULT_OUTPUT_CAP == 128
    assert evaluation.DEFAULT_REQUEST_CAP * evaluation.DEFAULT_OUTPUT_CAP == 15_360


@pytest.mark.asyncio
async def test_structured_output_failures_are_counted_and_usage_is_preserved(monkeypatch):
    dataset = evaluation.json.loads(evaluation.DATASET.read_text(encoding="utf-8"))
    case = dataset["cases"][0]

    async def invalid_decision(*_args, **_kwargs):
        raise PlannerDecisionFailure("AGENT_ACTION_INVALID", TokenUsage(17, 6, 23))

    monkeypatch.setattr(evaluation, "decide_next_action", invalid_decision)
    row = await evaluation._run_turn(
        case=case,
        turn=case["turns"][0],
        turn_index=0,
        arm="B2_AGENT_LOOP",
        history=[],
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        output_token_cap=128,
        global_budget={
            "requests": 0,
            "output_tokens": 0,
            "request_cap": 10,
            "exhausted": 0,
            "reported_overrun": 0,
        },
    )

    assert row["invalid_model_actions"] == 1
    assert row["output_usage"] == [
        {
            "prompt_tokens": 17,
            "output_tokens": 6,
            "total_tokens": 23,
        }
    ]


@pytest.mark.asyncio
async def test_planner_sends_json_context_and_reports_safe_parse_category(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                "choices": [
                    {
                        "message": {
                            "content": '{"kind":"ASK_USER","question":"ok","unexpected":true}'
                        }
                    }
                ],
            }

    requests = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **kwargs):
            requests.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(
        evaluation.decide_next_action.__globals__["httpx"], "AsyncClient", FakeClient
    )
    with pytest.raises(PlannerDecisionFailure) as error:
        await evaluation.decide_next_action(
            {"current_user_message": "请帮我安排"},
            {},
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
        )

    assert error.value.code == "AGENT_ACTION_INVALID"
    assert error.value.reason == "ACTION_SCHEMA_INVALID"
    assert error.value.usage.completion_tokens == 4
    parsed_context = evaluation.json.loads(requests[0]["messages"][1]["content"])
    assert parsed_context["observation"]["current_user_message"] == "请帮我安排"
    assert "do not use FINISH to replace a needed question" in requests[0]["messages"][0]["content"]
