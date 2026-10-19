"""Combine per-domain Agent traces into one user-visible turn trace."""

from typing import Any


def combine_agent_traces(
    traces: list[tuple[str, dict[str, Any] | None]],
    *, primary_stage: str | None = None,
) -> dict[str, Any] | None:
    usable = [(name, trace) for name, trace in traces if isinstance(trace, dict)]
    if not usable:
        return None

    primary_name, primary = next(
        ((name, trace) for name, trace in usable if name == primary_stage),
        usable[0],
    )
    stage_statuses = []
    transitions = []
    for stage_name, trace in usable:
        stage_status = {
            "stage": stage_name,
            "status": str(trace.get("status", "UNKNOWN"))[:32],
            "decision_source": str(trace.get("decision_source", "UNKNOWN"))[:32],
            "decision_summary": str(trace.get("decision_summary", ""))[:240],
        }
        for key in ("error_code", "failure_type"):
            value = trace.get(key)
            if isinstance(value, str) and value and value.replace("_", "").isalnum():
                stage_status[key] = value[:64].upper()
        stage_statuses.append(stage_status)
        stage_transitions = trace.get("transitions")
        if not isinstance(stage_transitions, list):
            continue
        for transition in stage_transitions[:32]:
            if isinstance(transition, dict):
                transitions.append({**transition, "stage": stage_name})

    statuses = [item["status"] for item in stage_statuses]
    overall_status = (
        "NEEDS_USER_INPUT" if "NEEDS_USER_INPUT" in statuses
        else next((status for status in ("FAILED", "REJECTED", "FALLBACK") if status in statuses), None)
        or ("SUCCEEDED" if all(status == "SUCCEEDED" for status in statuses) else primary.get("status", "UNKNOWN"))
    )
    return {
        **primary,
        "status": overall_status,
        "primary_stage": primary_name,
        "stage_statuses": stage_statuses,
        "transitions": transitions[:64],
    }
