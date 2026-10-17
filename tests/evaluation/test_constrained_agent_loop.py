import pytest
from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
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
