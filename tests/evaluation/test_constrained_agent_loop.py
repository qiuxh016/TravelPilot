import pytest
from pydantic import BaseModel, ValidationError
from travelpilot_core.agents.loop import (
    AgentAction,
    AgentTaskContext,
    LoopCheckpoint,
    run_constrained_loop,
)
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolSpec


class Input(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_loop_checkpoints_observe_act_verify_and_finish():
    checkpoints = []
    actions = iter([
        AgentAction(kind="CALL_TOOL", tool="lookup", arguments={"value": 4}),
        AgentAction(kind="FINISH", summary="done"),
    ])
    runtime = AgentRuntime([ToolSpec("lookup", Input, lambda data: _result(data.value))], _trace)

    async def policy(_obs, _allowed):
        return next(actions)

    result = await run_constrained_loop(
        runtime=runtime, policy=policy,
        verifier=_verified, checkpoint=_save(checkpoints),
    )
    assert result.status == "SUCCEEDED"
    assert result.observation["lookup"] == {"value": 4}
    assert [item["state"] for item in checkpoints] == ["OBSERVING", "ACTING", "VERIFIED", "SUCCEEDED"]


@pytest.mark.asyncio
async def test_loop_rejects_unlisted_tool_without_executing_it():
    checkpoints = []
    called = False

    async def handler(_data):
        nonlocal called
        called = True
        return {}

    async def policy(_obs, _allowed):
        return {"kind": "CALL_TOOL", "tool": "delete_trip", "arguments": {}}

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler)], _trace)
    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=_bool(True), checkpoint=_save(checkpoints),
    )
    assert result.status == "FALLBACK"
    assert result.error == "TOOL_NOT_ALLOWLISTED"
    assert not called


@pytest.mark.asyncio
async def test_loop_fails_closed_when_verifier_rejects_tool_output():
    checkpoints = []
    runtime = AgentRuntime([ToolSpec("lookup", Input, lambda data: _result(data.value))], _trace)

    async def policy(_obs, _allowed):
        return {"kind": "CALL_TOOL", "tool": "lookup", "arguments": {"value": 1}}

    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=_bool(False), checkpoint=_save(checkpoints),
    )
    assert result.status == "REJECTED"
    assert checkpoints[-1]["error"] == "VERIFICATION_FAILED"


@pytest.mark.asyncio
async def test_loop_persists_provider_failure_state_for_retry_or_fallback():
    from travelpilot_core.ports.tools import ToolFailure

    checkpoints = []

    async def handler(_data):
        raise ToolFailure("MAP_UNAVAILABLE")

    async def policy(_obs, _allowed):
        return {"kind": "CALL_TOOL", "tool": "lookup", "arguments": {"value": 2}}

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler, retry_limit=0)], _trace)
    result = await run_constrained_loop(
        runtime=runtime, policy=policy, verifier=_bool(True), checkpoint=_save(checkpoints),
    )
    assert result.status == "FAILED"
    assert result.error == "MAP_UNAVAILABLE"
    assert checkpoints[-1]["state"] == "FAILED"


@pytest.mark.asyncio
async def test_loop_can_recover_from_tool_failure_without_repeating_failed_call():
    from travelpilot_core.ports.tools import ToolFailure

    checkpoints = []
    calls = 0

    async def handler(_data):
        nonlocal calls
        calls += 1
        raise ToolFailure("MAP_UNAVAILABLE")

    async def policy(observation, _allowed):
        if "lookup_failure" in observation:
            return AgentAction(kind="FINISH", summary="route evidence unavailable")
        return AgentAction(kind="CALL_TOOL", tool="lookup", arguments={"value": 2})

    runtime = AgentRuntime([ToolSpec("lookup", Input, handler, retry_limit=0)], _trace)
    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=_bool(True),
        checkpoint=_save(checkpoints),
        max_decisions=2,
        continue_on_tool_failure=True,
    )

    assert result.status == "SUCCEEDED"
    assert result.observation["lookup_failure"] == {"status": "FAILED", "error": "MAP_UNAVAILABLE"}
    assert calls == 1
    assert [item["state"] for item in checkpoints] == [
        "OBSERVING", "ACTING", "FAILED", "OBSERVING_AFTER_FAILURE", "SUCCEEDED",
    ]


@pytest.mark.asyncio
async def test_loop_enforces_decision_limit():
    checkpoints = []

    async def policy(_obs, _allowed):
        return {"kind": "CALL_TOOL", "tool": "lookup", "arguments": {"value": 2}}

    runtime = AgentRuntime([ToolSpec("lookup", Input, lambda data: _result(data.value))], _trace)
    result = await run_constrained_loop(
        runtime=runtime, policy=policy, verifier=_bool(True), checkpoint=_save(checkpoints),
        max_decisions=1,
    )
    assert result.status == "STEP_LIMIT"
    assert checkpoints[-1]["state"] == "STEP_LIMIT"


@pytest.mark.asyncio
async def test_loop_recovers_allowlisted_decision_without_calling_policy_again():
    checkpoints = []
    tool_calls = []
    runtime = AgentRuntime(
        [ToolSpec("lookup", Input, lambda data: _record_lookup(data.value, tool_calls))], _trace,
    )
    policy_calls = 0
    recovery_calls = []

    async def policy(_obs, _allowed):
        nonlocal policy_calls
        policy_calls += 1
        raise AssertionError("recovered decision should bypass model policy")

    async def recover(observation, allowed_tools, decision_number):
        recovery_calls.append((sorted(observation), allowed_tools, decision_number))
        if decision_number == 1:
            return AgentAction(kind="CALL_TOOL", tool="lookup", arguments={"value": 4})
        return None

    async def finish_after_recovery(_obs, _allowed):
        return AgentAction(kind="FINISH", summary="done")

    result = await run_constrained_loop(
        runtime=runtime,
        policy=finish_after_recovery,
        verifier=_verified,
        checkpoint=_save(checkpoints),
        recover_decision=recover,
    )

    assert result.status == "SUCCEEDED"
    assert policy_calls == 0
    assert tool_calls == [4]
    assert recovery_calls == [([], ["lookup"], 1), (["lookup"], ["lookup"], 2)]


@pytest.mark.asyncio
async def test_loop_persists_only_fresh_allowlisted_tool_decisions():
    checkpoints = []
    persisted = []
    runtime = AgentRuntime([ToolSpec("lookup", Input, lambda data: _result(data.value))], _trace)
    actions = iter([
        AgentAction(kind="CALL_TOOL", tool="lookup", arguments={"value": 4}),
        AgentAction(kind="FINISH", summary="done"),
    ])

    async def policy(_obs, _allowed):
        return next(actions)

    async def persist(observation, action, decision_number):
        persisted.append((dict(observation), action.tool, decision_number))

    async def recover(_observation, _allowed, decision_number):
        if decision_number == 2:
            return AgentAction(kind="FINISH", summary="recovered finish")
        return None

    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=_verified,
        checkpoint=_save(checkpoints),
        recover_decision=recover,
        persist_decision=persist,
    )

    assert result.status == "SUCCEEDED"
    assert len(persisted) == 1
    assert persisted[0][1:] == ("lookup", 1)


async def _record_lookup(value, calls):
    calls.append(value)
    return {"value": value}


async def _result(value):
    return {"value": value}


async def _trace(_data):
    return None


def _save(items):
    async def save(value):
        items.append(value)
    return save


async def _verified(_tool, output):
    return output.get("value") == 4


def _bool(value):
    async def result(_tool, _output):
        return value
    return result
def test_agent_action_contract_rejects_malformed_decisions():
    with pytest.raises(ValidationError):
        AgentAction(kind="CALL_TOOL")
    with pytest.raises(ValidationError):
        AgentAction(kind="FINISH", tool="search_places")
    with pytest.raises(ValidationError):
        AgentAction(kind="ASK_USER")
    with pytest.raises(ValidationError):
        AgentAction(kind="FINISH", unexpected="ignored")


def test_agent_action_contract_accepts_all_supported_decisions():
    assert AgentAction(kind="CALL_TOOL", tool="search_places", arguments={"keyword": "博物馆"})
    assert AgentAction(kind="ASK_USER", question="偏好哪类地点？")
    assert AgentAction(kind="FINISH", summary="已完成")


def test_loop_checkpoint_contract_rejects_incomplete_or_sensitive_events():
    with pytest.raises(ValidationError):
        LoopCheckpoint(state="NEEDS_USER_INPUT", transition=1)
    with pytest.raises(ValidationError):
        LoopCheckpoint(state="ACTING", transition=1)
    with pytest.raises(ValidationError):
        LoopCheckpoint(state="SUCCEEDED", transition=1, raw_observation={"secret": "value"})
    assert LoopCheckpoint(
        state="VERIFIED", transition=1, tool="lookup", observation_keys=["lookup"],
    ).state == "VERIFIED"


def test_task_context_contract_rejects_unknown_fields_and_duplicate_scope():
    with pytest.raises(ValidationError):
        AgentTaskContext(
            task_type="TRANSPORT", goal="查询路线", success_criteria=["有路线"],
            allowed_tools=["get_driving_route", "get_driving_route"],
            approval_boundary="READ_ONLY", unknown="value",
        )


def test_task_context_contract_rejects_facts_marked_both_known_and_missing():
    with pytest.raises(ValidationError, match="both known and missing"):
        AgentTaskContext(
            task_type="SCHEDULE", goal="安排已收藏地点",
            known_facts=["trip_dates"], missing_facts=["trip_dates"],
            success_criteria=["produce a feasible draft"],
            approval_boundary="DRAFT_ONLY",
        )


@pytest.mark.asyncio
async def test_loop_exposes_task_contract_and_intersects_tool_scopes():
    runtime = AgentRuntime([ToolSpec("lookup", Input, lambda data: _result(data.value))], _trace)
    seen = {}

    async def policy(observation, allowed_tools):
        seen["task"] = observation["agent_task"]
        seen["allowed"] = allowed_tools
        return AgentAction(kind="FINISH", summary="no tool needed")

    task = AgentTaskContext(
        task_type="DISCOVERY", goal="查地点", success_criteria=["返回候选"],
        allowed_tools=[], approval_boundary="READ_ONLY",
    )
    await run_constrained_loop(
        runtime=runtime, policy=policy, verifier=_verified,
        checkpoint=_save([]), task_context=task,
    )
    assert seen["task"]["goal"] == "查地点"
    assert seen["allowed"] == []
