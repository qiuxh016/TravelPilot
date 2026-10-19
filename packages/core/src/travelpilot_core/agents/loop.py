"""Finite, checkpointable loop for allowlisted read-only agent actions."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure


class AgentAction(BaseModel):
    """Validated decision envelope shared by constrained Advisor loops."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["CALL_TOOL", "ASK_USER", "FINISH"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=240)
    question: str | None = None

    @model_validator(mode="after")
    def validate_action_shape(self):
        if self.kind == "CALL_TOOL" and not self.tool:
            raise ValueError("CALL_TOOL requires a tool name")
        if self.kind != "CALL_TOOL" and (self.tool is not None or self.arguments):
            raise ValueError("Only CALL_TOOL may include a tool name or arguments")
        if self.kind == "ASK_USER":
            question = (self.question or self.summary).strip()
            if not question:
                raise ValueError("ASK_USER requires a question")
            if len(question) > 500:
                raise ValueError("ASK_USER question exceeds 500 characters")
        elif self.question is not None:
            raise ValueError("Only ASK_USER may include a question")
        return self


class LoopCheckpoint(BaseModel):
    """Validated, privacy-safe lifecycle event emitted by every constrained loop."""

    model_config = ConfigDict(extra="forbid")

    state: Literal[
        "OBSERVING", "FALLBACK", "SUCCEEDED", "NEEDS_USER_INPUT", "ACTING",
        "FAILED", "OBSERVING_AFTER_FAILURE", "REJECTED", "OBSERVING_AFTER_REJECTION",
        "VERIFIED", "STEP_LIMIT",
    ]
    transition: int = Field(ge=0, le=100)
    tool: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=240)
    question: str | None = Field(default=None, max_length=500)
    observation_keys: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_checkpoint_shape(self):
        if self.state == "NEEDS_USER_INPUT" and not (self.question and self.question.strip()):
            raise ValueError("NEEDS_USER_INPUT requires a question")
        if self.state in {"ACTING", "FAILED", "REJECTED", "VERIFIED", "OBSERVING_AFTER_FAILURE", "OBSERVING_AFTER_REJECTION"} and not self.tool:
            raise ValueError(f"{self.state} requires a tool name")
        if self.state in {"FAILED", "REJECTED"} and not self.error:
            raise ValueError(f"{self.state} requires an error code")
        if self.state == "FALLBACK" and not self.reason:
            raise ValueError("FALLBACK requires a reason")
        if any(not key or len(key) > 80 for key in self.observation_keys):
            raise ValueError("observation keys must be nonempty and bounded")
        return self


class AgentTaskContext(BaseModel):
    """Shared task contract supplied to every outcome-specific policy."""

    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "DISCOVERY", "SCHEDULE", "SEARCH", "MEAL", "LODGING", "TRANSPORT",
        "WEATHER", "PREPARATION", "MEMORY", "GENERAL",
    ]
    goal: str = Field(min_length=1, max_length=1200)
    known_facts: list[str] = Field(default_factory=list, max_length=16)
    missing_facts: list[str] = Field(default_factory=list, max_length=16)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    allowed_tools: list[str] = Field(default_factory=list, max_length=12)
    approval_boundary: Literal["READ_ONLY", "DRAFT_ONLY", "USER_CONFIRMATION_REQUIRED"]

    @model_validator(mode="after")
    def validate_task_contract(self):
        for values in (self.known_facts, self.missing_facts, self.allowed_tools, self.success_criteria):
            if any(not value.strip() or len(value) > 120 for value in values):
                raise ValueError("task contract entries must be nonempty and bounded")
            if len(set(values)) != len(values):
                raise ValueError("task contract entries must be unique")
        overlap = set(self.known_facts) & set(self.missing_facts)
        if overlap:
            raise ValueError(
                "task facts cannot be both known and missing: "
                + ", ".join(sorted(overlap))
            )
        return self


@dataclass(frozen=True)
class LoopResult:
    status: Literal["SUCCEEDED", "NEEDS_USER_INPUT", "FALLBACK", "FAILED", "REJECTED", "STEP_LIMIT"]
    observation: dict[str, Any]
    transitions: list[dict[str, Any]]
    error: str | None = None
    pending_question: str | None = None


ActionPolicy = Callable[[dict[str, Any], list[str]], Awaitable[AgentAction]]
Verifier = Callable[[str, dict[str, Any]], Awaitable[bool]]
Checkpoint = Callable[[dict[str, Any]], Awaitable[None]]
DecisionRecovery = Callable[
    [dict[str, Any], list[str], int], Awaitable[AgentAction | dict[str, Any] | None]
]
DecisionCheckpoint = Callable[[dict[str, Any], AgentAction, int], Awaitable[None]]


async def run_constrained_loop(
    *, runtime: AgentRuntime, policy: ActionPolicy, verifier: Verifier,
    checkpoint: Checkpoint, initial_observation: dict[str, Any] | None = None,
    task_context: AgentTaskContext | None = None,
    max_decisions: int = 4, allowed_tool_names: set[str] | None = None,
    continue_on_tool_failure: bool = False, continue_on_verifier_failure: bool = False,
    recover_decision: DecisionRecovery | None = None,
    persist_decision: DecisionCheckpoint | None = None,
) -> LoopResult:
    """Run bounded calls. Tool execution is delegated to Runtime's allowlist.

    Checkpoints deliberately contain action names and statuses only; callers should
    persist sensitive observations separately under their normal data policy.
    """
    observation = dict(initial_observation or {})
    transitions: list[dict[str, Any]] = []
    scope = set(runtime.tools)
    if allowed_tool_names is not None:
        scope &= allowed_tool_names
    if task_context is not None:
        scope &= set(task_context.allowed_tools)
        observation["agent_task"] = task_context.model_dump(mode="json")
    allowed_tools = sorted(scope)

    async def emit_checkpoint(**values: Any) -> None:
        event = LoopCheckpoint.model_validate(values).model_dump(exclude_none=True)
        await checkpoint(event)

    await emit_checkpoint(state="OBSERVING", transition=0, observation_keys=sorted(observation))
    for decision_number in range(1, max_decisions + 1):
        try:
            recovered = (
                await recover_decision(observation, allowed_tools, decision_number)
                if recover_decision is not None else None
            )
            action = recovered if recovered is not None else await policy(observation, allowed_tools)
            if not isinstance(action, AgentAction):
                action = AgentAction.model_validate(action)
        except (ValidationError, ValueError, TypeError):
            await emit_checkpoint(state="FALLBACK", transition=decision_number, reason="INVALID_ACTION")
            return LoopResult("FALLBACK", observation, transitions, "INVALID_ACTION")
        if action.kind == "FINISH":
            await emit_checkpoint(state="SUCCEEDED", transition=decision_number, summary=action.summary[:240])
            return LoopResult("SUCCEEDED", observation, transitions)
        if action.kind == "ASK_USER":
            question = (action.question or action.summary).strip()[:500]
            if not question:
                await emit_checkpoint(state="FALLBACK", transition=decision_number, reason="EMPTY_USER_QUESTION")
                return LoopResult("FALLBACK", observation, transitions, "EMPTY_USER_QUESTION")
            await emit_checkpoint(state="NEEDS_USER_INPUT", transition=decision_number, question=question)
            return LoopResult("NEEDS_USER_INPUT", observation, transitions,
                              pending_question=question)
        if not action.tool or action.tool not in allowed_tools:
            await emit_checkpoint(state="FALLBACK", transition=decision_number, reason="TOOL_NOT_ALLOWLISTED")
            return LoopResult("FALLBACK", observation, transitions, "TOOL_NOT_ALLOWLISTED")
        if persist_decision is not None and recovered is None:
            await persist_decision(observation, action, decision_number)
        await emit_checkpoint(state="ACTING", transition=decision_number, tool=action.tool)
        try:
            result = await runtime.call(action.tool, action.arguments)
        except ToolFailure as exc:
            transitions.append({"tool": action.tool, "status": "FAILED", "error": exc.code})
            await emit_checkpoint(state="FAILED", transition=decision_number, tool=action.tool, error=exc.code)
            if continue_on_tool_failure:
                observation[f"{action.tool}_failure"] = {"status": "FAILED", "error": exc.code}
                await emit_checkpoint(state="OBSERVING_AFTER_FAILURE", transition=decision_number,
                                      tool=action.tool, error=exc.code,
                                      observation_keys=sorted(observation))
                continue
            return LoopResult("FAILED", observation, transitions, exc.code)
        if not await verifier(action.tool, result):
            transitions.append({"tool": action.tool, "status": "REJECTED", "error": "VERIFICATION_FAILED"})
            await emit_checkpoint(state="REJECTED", transition=decision_number,
                                  tool=action.tool, error="VERIFICATION_FAILED")
            if continue_on_verifier_failure:
                observation[f"{action.tool}_verification_failure"] = {
                    "status": "REJECTED", "error": "VERIFICATION_FAILED",
                }
                await emit_checkpoint(state="OBSERVING_AFTER_REJECTION", transition=decision_number,
                                      tool=action.tool, observation_keys=sorted(observation))
                continue
            return LoopResult("REJECTED", observation, transitions, "VERIFICATION_FAILED")
        observation[action.tool] = result
        transitions.append({"tool": action.tool, "status": "VERIFIED"})
        await emit_checkpoint(state="VERIFIED", transition=decision_number,
                              tool=action.tool, observation_keys=sorted(observation))
    await emit_checkpoint(state="STEP_LIMIT", transition=max_decisions)
    return LoopResult("STEP_LIMIT", observation, transitions, "STEP_LIMIT")
