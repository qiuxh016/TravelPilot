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
    metric_totals: dict[str, int] = {}
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
        metrics = trace.get("execution_metrics")
        if isinstance(metrics, dict):
            for key in (
                "logical_steps", "tool_attempts", "retries", "input_tokens",
                "output_tokens", "total_tokens",
            ):
                value = metrics.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    metric_totals[key] = metric_totals.get(key, 0) + value
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
    safe_transitions = transitions[:64]
    tool_duration_ms = sum(
        max(0, item["latency_ms"])
        for item in safe_transitions
        if isinstance(item.get("latency_ms"), int) and not isinstance(item.get("latency_ms"), bool)
    )
    return {
        **primary,
        "status": overall_status,
        "primary_stage": primary_name,
        "stage_statuses": stage_statuses,
        "transitions": safe_transitions,
        "execution_metrics": metric_totals,
        "tool_duration_ms": tool_duration_ms,
    }
