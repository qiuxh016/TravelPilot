"""Typed, user-visible lifecycle snapshot for the latest Advisor task."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travelpilot_api.advisor.task_taxonomy import AdvisorTaskType


class AdvisorTaskSnapshot(BaseModel):
    """Bounded state needed to explain or resume a multi-turn Advisor task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    task_type: AdvisorTaskType
    status: Literal["WAITING_USER", "PROPOSED", "COMPLETED", "PARTIAL", "CANCELLED", "STALE", "FAILED"]
    goal: str = Field(min_length=1, max_length=1200)
    context_revision: int = Field(ge=0)
    turn_number: int = Field(ge=1, le=100)
    answers: list[str] = Field(default_factory=list, max_length=6)
    question: str | None = Field(default=None, max_length=500)
    action_summary: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if self.status == "WAITING_USER" and not (self.question and self.question.strip()):
            raise ValueError("WAITING_USER tasks require a user question")
        if self.status != "WAITING_USER" and self.question is not None:
            raise ValueError("Only WAITING_USER tasks may have a pending question")
        if self.turn_number < len(self.answers) + 1:
            raise ValueError("turn_number cannot be behind the recorded answer count")
        return self


def resolve_task_status(
    *,
    stale: bool = False,
    cancelled: bool = False,
    waiting_for_user: bool = False,
    proposed: bool = False,
    has_partial_results: bool = False,
    tool_failed: bool = False,
    loop_statuses: list[str] | tuple[str, ...] = (),
) -> Literal["WAITING_USER", "PROPOSED", "COMPLETED", "PARTIAL", "CANCELLED", "STALE", "FAILED"]:
    """Map domain-loop outcomes to one consistent user-visible task state."""
    if stale:
        return "STALE"
    if cancelled:
        return "CANCELLED"
    if waiting_for_user:
        return "WAITING_USER"

    failed_statuses = {"FAILED", "REJECTED", "FALLBACK", "STEP_LIMIT"}
    has_failure = tool_failed or any(status in failed_statuses for status in loop_statuses)
    if has_failure:
        return "PARTIAL" if has_partial_results or proposed else "FAILED"
    if proposed:
        return "PROPOSED"
    return "COMPLETED"


def restore_task_snapshot(value: object) -> AdvisorTaskSnapshot | None:
    """Load a session snapshot without allowing malformed context to break reads."""
    if not isinstance(value, dict):
        return None
    try:
        return AdvisorTaskSnapshot.model_validate(value)
    except (TypeError, ValueError):
        return None
