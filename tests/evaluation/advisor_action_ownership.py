"""Capability ownership for Advisor multi-turn evaluation actions.

This registry classifies who performs each expected outcome. It does not claim
that a frozen case has been executed by the production Agent.
"""

from __future__ import annotations

from typing import Any, Literal

ActionOwner = Literal[
    "AGENT_DECISION", "AGENT_RESPONSE", "PRODUCT_WORKFLOW",
    "USER_APPROVAL", "RUNTIME_GUARD",
]

USER_APPROVAL_ACTIONS = {
    "ASK_CONFIRMATION", "ASK_CONFIRMATION_FOR_BOTH_CHANGES",
    "ASK_MEMORY_CONSENT", "ASK_MEMORY_UPDATE_CONSENT", "COMMIT_CONFIRMED_CANDIDATES",
    "COMMIT_CONFIRMED_DRAFT", "DELETE_CONFIRMED_MEMORY", "PROPOSE_MEMORY",
    "SAVE_CONFIRMED_MEMORY", "UPDATE_CONFIRMED_MEMORY", "USE_SESSION_ONLY",
}

PRODUCT_WORKFLOW_ACTIONS = {
    "EXTRACT_CANDIDATES", "LIST_OWNER_MEMORIES", "OCR_EXTRACT",
    "RECOMMEND_DESTINATIONS", "SET_MEAL_TIME", "SHOW_UNCERTAIN_OCR",
    "SHOW_UNVERIFIED_CANDIDATES", "VERIFY_OPENING_HOURS",
}

RUNTIME_GUARD_ACTIONS = {
    "CANCEL_PENDING_TASK", "DECLINE_SENSITIVE_INFERENCE", "EXPLAIN_LINK_UNAVAILABLE",
    "EXPLAIN_MEMORY_DISABLED", "EXPLAIN_NO_EXTERNAL_BOOKING",
    "EXPLAIN_UNSUPPORTED_BOOKING", "KEEP_DRAFT_UNCOMMITTED", "REJECT_INVALID_ACTION",
    "REJECT_UNOWNED_PLACE", "REPORT_TOOL_FAILURE", "REUSE_IDEMPOTENT_RESULT",
    "REVALIDATE_TRIP_CONTEXT", "SAFE_STOP", "STOP_AFTER_BOUNDED_RETRIES",
}

AGENT_DECISION_WITHOUT_TOOLS = {
    "ASK_USER", "FINISH", "ASK_USER_FOR_SECOND_ENDPOINT", "ASK_USER_OR_LABEL_ASSUMPTION",
    "ASK_USER_OR_LABEL_UNVERIFIED", "ASK_USER_TO_RELAX_CONSTRAINT",
}


def action_owner(turn: dict[str, Any]) -> ActionOwner:
    """Return the owner of the expected outcome, separate from tool availability."""
    action = turn.get("expected_action")
    if action in USER_APPROVAL_ACTIONS:
        return "USER_APPROVAL"
    if action in PRODUCT_WORKFLOW_ACTIONS:
        return "PRODUCT_WORKFLOW"
    if action in RUNTIME_GUARD_ACTIONS:
        return "RUNTIME_GUARD"
    if turn.get("allowed_tools") or action in AGENT_DECISION_WITHOUT_TOOLS:
        return "AGENT_DECISION"
    return "AGENT_RESPONSE"


def owner_counts(dataset: dict[str, Any]) -> dict[str, int]:
    counts = {name: 0 for name in (
        "AGENT_DECISION", "AGENT_RESPONSE", "PRODUCT_WORKFLOW",
        "USER_APPROVAL", "RUNTIME_GUARD",
    )}
    for case in dataset.get("cases", []):
        for turn in case.get("turns", []):
            counts[action_owner(turn)] += 1
    return counts
