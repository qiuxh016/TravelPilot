"""Finite, checkpointable loop for allowlisted read-only agent actions."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure


class AgentAction(BaseModel):
    kind: Literal["CALL_TOOL", "FINISH"]
    tool: str | None = None
    arguments: dict[str, Any] = {}
    summary: str = ""


@dataclass(frozen=True)
class LoopResult:
    status: str
    observation: dict[str, Any]
    transitions: list[dict[str, Any]]
    error: str | None = None


ActionPolicy = Callable[[dict[str, Any], list[str]], Awaitable[AgentAction]]
Verifier = Callable[[str, dict[str, Any]], Awaitable[bool]]
Checkpoint = Callable[[dict[str, Any]], Awaitable[None]]


async def run_constrained_loop(
    *, runtime: AgentRuntime, policy: ActionPolicy, verifier: Verifier,
    checkpoint: Checkpoint, initial_observation: dict[str, Any] | None = None,
    max_decisions: int = 4, allowed_tool_names: set[str] | None = None,
) -> LoopResult:
    """Run bounded calls. Tool execution is delegated to Runtime's allowlist.

    Checkpoints deliberately contain action names and statuses only; callers should
    persist sensitive observations separately under their normal data policy.
    """
    observation = dict(initial_observation or {})
    transitions: list[dict[str, Any]] = []
    allowed_tools = sorted(set(runtime.tools) & allowed_tool_names if allowed_tool_names is not None else runtime.tools)
    await checkpoint({"state": "OBSERVING", "transition": 0, "observation_keys": sorted(observation)})
    for decision_number in range(1, max_decisions + 1):
        try:
            action = await policy(observation, allowed_tools)
            if not isinstance(action, AgentAction):
                action = AgentAction.model_validate(action)
        except (ValidationError, ValueError, TypeError):
            await checkpoint({"state": "FALLBACK", "transition": decision_number,
                              "reason": "INVALID_ACTION"})
            return LoopResult("FALLBACK", observation, transitions, "INVALID_ACTION")
        if action.kind == "FINISH":
            await checkpoint({"state": "SUCCEEDED", "transition": decision_number,
                              "summary": action.summary[:240]})
            return LoopResult("SUCCEEDED", observation, transitions)
        if not action.tool or action.tool not in allowed_tools:
            await checkpoint({"state": "FALLBACK", "transition": decision_number,
                              "reason": "TOOL_NOT_ALLOWLISTED"})
            return LoopResult("FALLBACK", observation, transitions, "TOOL_NOT_ALLOWLISTED")
        await checkpoint({"state": "ACTING", "transition": decision_number, "tool": action.tool})
        try:
            result = await runtime.call(action.tool, action.arguments)
        except ToolFailure as exc:
            transitions.append({"tool": action.tool, "status": "FAILED", "error": exc.code})
            await checkpoint({"state": "FAILED", "transition": decision_number,
                              "tool": action.tool, "error": exc.code})
            return LoopResult("FAILED", observation, transitions, exc.code)
        if not await verifier(action.tool, result):
            transitions.append({"tool": action.tool, "status": "REJECTED", "error": "VERIFICATION_FAILED"})
            await checkpoint({"state": "REJECTED", "transition": decision_number,
                              "tool": action.tool, "error": "VERIFICATION_FAILED"})
            return LoopResult("REJECTED", observation, transitions, "VERIFICATION_FAILED")
        observation[action.tool] = result
        transitions.append({"tool": action.tool, "status": "VERIFIED"})
        await checkpoint({"state": "VERIFIED", "transition": decision_number,
                          "tool": action.tool, "observation_keys": sorted(observation)})
    await checkpoint({"state": "STEP_LIMIT", "transition": max_decisions})
    return LoopResult("STEP_LIMIT", observation, transitions, "STEP_LIMIT")
