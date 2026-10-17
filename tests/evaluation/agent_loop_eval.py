"""Deterministic acceptance scenarios for the constrained agent loop."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure, ToolSpec


class _Input(BaseModel):
    value: int


async def _run_case(case_id: str) -> dict[str, Any]:
    checkpoints = []

    async def trace(_entry: dict) -> None:
        return None

    async def read(data: _Input) -> dict[str, int]:
        if case_id == "provider_failure":
            raise ToolFailure("MAP_UNAVAILABLE")
        return {"value": data.value}

    runtime = AgentRuntime([ToolSpec("lookup", _Input, read, retry_limit=0)], trace)
    decisions = 0

    async def policy(_observation: dict, _allowed: list[str]) -> AgentAction:
        nonlocal decisions
        decisions += 1
        if case_id == "verified_read_and_finish" and decisions == 2:
            return AgentAction(kind="FINISH", summary="verified")
        return AgentAction(kind="CALL_TOOL", tool="lookup", arguments={"value": 4})

    async def verifier(_tool: str, output: dict) -> bool:
        return case_id != "verifier_rejection" and output.get("value") == 4

    async def checkpoint(state: dict) -> None:
        checkpoints.append(state["state"])

    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=checkpoint,
        max_decisions=1 if case_id == "decision_limit" else 3,
        allowed_tool_names={"lookup"},
    )
    expected = {
        "verified_read_and_finish": "SUCCEEDED",
        "verifier_rejection": "REJECTED",
        "provider_failure": "FAILED",
        "decision_limit": "STEP_LIMIT",
    }[case_id]
    return {
        "case_id": case_id,
        "status": result.status,
        "expected_status": expected,
        "expected_match": result.status == expected,
        "decision_count": decisions,
        "tool_attempt_count": runtime.tool_attempts,
        "verified_tool_count": sum(item["status"] == "VERIFIED" for item in result.transitions),
        "checkpoint_states": checkpoints,
    }


def run_evaluation() -> dict[str, Any]:
    case_ids = (
        "verified_read_and_finish",
        "verifier_rejection",
        "provider_failure",
        "decision_limit",
    )
    results = [asyncio.run(_run_case(case_id)) for case_id in case_ids]
    return {
        "evaluation_id": "constrained_agent_loop_v1",
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "scenario_count": len(results),
        "all_expectations_match": all(item["expected_match"] for item in results),
        "scope": "loop state and recovery boundaries only; no live model or provider quality claim",
        "results": results,
    }
