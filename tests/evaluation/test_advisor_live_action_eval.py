import pytest
from travelpilot_core.agents.loop import AgentAction
from travelpilot_core.agents.planner import TokenUsage

from tests.evaluation import advisor_live_action_eval as live_eval
from tests.evaluation.advisor_live_action_eval import validate_live_limits


def test_live_runner_requires_small_explicit_request_and_output_caps():
    validate_live_limits(6, 128)
    validate_live_limits(1, 1)

    with pytest.raises(ValueError):
        validate_live_limits(7, 128)
    with pytest.raises(ValueError):
        validate_live_limits(6, 129)
    with pytest.raises(ValueError):
        validate_live_limits(0, 128)


def test_live_runner_allocates_six_requests_to_recovery_and_verifier_cases():
    timeout_state = {"route_matrix": "provider_timeout"}
    malformed_state = {"route_matrix": "malformed"}

    assert live_eval._decision_limit("B2_AGENT_LOOP", timeout_state) == 2
    assert live_eval._decision_limit("B3_VERIFIER_RECOVERY", timeout_state) == 2
    assert live_eval._decision_limit("B2_AGENT_LOOP", malformed_state) == 2
    assert live_eval._decision_limit("B3_VERIFIER_RECOVERY", malformed_state) == 2


@pytest.mark.asyncio
async def test_live_runner_exercises_failure_recovery_and_matrix_verifier_without_network(monkeypatch):
    calls = 0

    async def fake_decide(observation, _schemas, **_kwargs):
        nonlocal calls
        calls += 1
        if "get_distance_matrix_failure" in observation:
            action = AgentAction(
                kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                summary="use local route estimates after provider failure",
            )
        elif "get_distance_matrix" in observation:
            action = AgentAction(
                kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                summary="build a proposal from the returned route matrix",
            )
        else:
            action = AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={})
        return action, TokenUsage(prompt_tokens=20, completion_tokens=12, total_tokens=32)

    monkeypatch.setattr(live_eval, "decide_next_action", fake_decide)
    report = await live_eval.run_live_eval(
        api_key="test-key",
        model="test-model",
        base_url="http://127.0.0.1:1/v1",
        request_limit=6,
        output_token_cap=128,
    )

    assert calls == report["requests_used"] == 6
    assert report["source_provenance"]["git_revision"]
    assert len(report["source_provenance"]["source_sha256"][
        "tests/evaluation/advisor_live_action_eval.py"
    ]) == 64
    assert report["run_complete"] is True
    assert report["model_action_failures"] == 0
    assert report["draft_ready_cases"] == 3
    assert report["all_model_actions_valid"] is True
    timeout_b3 = report["groups"]["B3_VERIFIER_RECOVERY"][0]
    assert timeout_b3["final_state"] == "DRAFT_READY"
    assert timeout_b3["state"] == "DRAFT_READY"
    assert timeout_b3["loop_status"] == "STEP_LIMIT"
    assert timeout_b3["hard_invariants"]["no_repeated_failed_route_call"] is True
    assert timeout_b3["draft_summary"]["feasible"] is True
    assert timeout_b3["draft_summary"]["candidate_count"] >= 2
    assert timeout_b3["draft_summary"]["route_warning"] is True
    malformed_b2, malformed_b3 = (
        report["groups"]["B2_AGENT_LOOP"][1],
        report["groups"]["B3_VERIFIER_RECOVERY"][1],
    )
    assert report["groups"]["B2_AGENT_LOOP"][0]["request_count"] == 1
    assert timeout_b3["request_count"] == 2
    assert malformed_b2["request_count"] == 2
    assert malformed_b3["request_count"] == 1
    assert malformed_b2["final_state"] == "DRAFT_READY"
    assert malformed_b2["draft_summary"]["route_warning"] is False
    assert malformed_b3["final_state"] == "DRAFT_READY"
    assert malformed_b3["draft_summary"]["route_warning"] is True
    assert any(action["kind"] == "SYSTEM_RECOVERY" for action in malformed_b3["actions"])
    assert malformed_b2["hard_invariants"]["malformed_route_not_accepted_as_verified"] is False
    assert malformed_b3["hard_invariants"]["malformed_route_not_accepted_as_verified"] is True


@pytest.mark.asyncio
async def test_live_runner_marks_partial_run_when_request_cap_prevents_paired_group(monkeypatch):
    calls = 0

    async def fake_decide(_observation, _schemas, **_kwargs):
        nonlocal calls
        calls += 1
        return (
            AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={}),
            TokenUsage(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        )

    monkeypatch.setattr(live_eval, "decide_next_action", fake_decide)
    report = await live_eval.run_live_eval(
        api_key="test-key",
        model="test-model",
        base_url="http://127.0.0.1:1/v1",
        request_limit=1,
        output_token_cap=128,
    )

    assert calls == report["requests_used"] == 1
    assert report["run_complete"] is False
    assert report["groups_complete"]["B2_AGENT_LOOP"] is False
    assert report["groups_complete"]["B3_VERIFIER_RECOVERY"] is False
