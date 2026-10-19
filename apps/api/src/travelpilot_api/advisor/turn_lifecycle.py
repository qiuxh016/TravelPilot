"""Resolve pending-question lifecycle at the start of an Advisor turn."""

from dataclasses import dataclass
from typing import Any

from travelpilot_api.advisor.state import (
    PendingAgentQuestion,
    cancels_pending_task,
    pending_agent_question,
    pending_agent_question_from_context,
    pending_question_matches_context,
    resume_pending_task,
)


@dataclass(frozen=True)
class PendingTurnResolution:
    pending_question: PendingAgentQuestion | None
    continuing_task: PendingAgentQuestion | None
    turn_goal: str
    invalidated: bool
    cancelled: bool
    cancelled_task_type: str | None


def resolve_pending_turn(
    *,
    context: dict[str, Any],
    previous_payload: Any,
    content: str,
    invalidated_by_context_update: bool = False,
) -> PendingTurnResolution:
    """Recover, invalidate, cancel or resume the pending task without I/O."""
    pending = pending_agent_question_from_context(context) or pending_agent_question(previous_payload)
    continuing_task = pending
    invalidated = bool(
        invalidated_by_context_update
        or (isinstance(previous_payload, dict) and previous_payload.get("invalidated_pending_task"))
        or (pending and not pending_question_matches_context(pending, context))
    )
    if invalidated:
        pending = None

    context.pop("pending_agent_question", None)
    cancelled = bool(pending and cancels_pending_task(content))
    cancelled_task_type = pending.task_type if cancelled and pending else None
    if cancelled:
        pending = None

    if pending and not cancelled:
        pending, turn_goal = resume_pending_task(pending, content)
    else:
        turn_goal = content

    return PendingTurnResolution(
        pending_question=pending,
        continuing_task=continuing_task,
        turn_goal=turn_goal,
        invalidated=invalidated,
        cancelled=cancelled,
        cancelled_task_type=cancelled_task_type,
    )
