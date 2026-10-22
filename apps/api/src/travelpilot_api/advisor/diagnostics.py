"""Privacy-preserving Advisor diagnostics export helpers."""

import re
from typing import Any

_METRIC_KEYS = {
    "logical_steps", "tool_attempts", "retries", "input_tokens",
    "output_tokens", "total_tokens", "output_token_budget",
}
_SAFE_METADATA_KEYS = {
    "result_count", "forecast_day_count", "forecast_status",
    "route_distance_m", "route_duration_min", "point_count",
    "decision_source", "route_evidence", "route_source",
    "plan_version", "draft_version", "context_revision",
}


def _safe_identifier(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value[:limit]
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]*", value) else None


def redact_advisor_event(event: Any) -> dict[str, Any]:
    """Export operational facts only; never copy arbitrary event metadata."""
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    safe_metadata: dict[str, Any] = {}
    for key in _SAFE_METADATA_KEYS:
        value = metadata.get(key)
        if (isinstance(value, (str, int, float, bool))
                and (not isinstance(value, str) or len(value) <= 40)):
            safe_metadata[key] = value
    metrics = metadata.get("execution_metrics")
    if isinstance(metrics, dict):
        safe_metrics = {
            key: value for key, value in metrics.items()
            if key in _METRIC_KEYS and isinstance(value, int)
            and not isinstance(value, bool) and value >= 0
        }
        if safe_metrics:
            safe_metadata["execution_metrics"] = safe_metrics
    transitions = metadata.get("transitions")
    if isinstance(transitions, list):
        safe_transitions = []
        for item in transitions[:64]:
            if not isinstance(item, dict):
                continue
            safe_item: dict[str, Any] = {}
            for key in ("tool", "stage", "status", "error", "error_code"):
                value = _safe_identifier(item.get(key), 80)
                if value is not None:
                    safe_item[key] = value
            latency = item.get("latency_ms")
            if isinstance(latency, int) and not isinstance(latency, bool) and latency >= 0:
                safe_item["latency_ms"] = latency
            safe_transitions.append(safe_item)
        if safe_transitions:
            safe_metadata["transitions"] = safe_transitions
    created_at = getattr(event, "created_at", None)
    return {
        "phase": str(event.phase)[:32],
        "operation": str(event.operation)[:80],
        "status": str(event.status)[:24],
        "duration_ms": max(0, int(event.duration_ms or 0)),
        "token_usage": max(0, int(event.token_usage or 0)),
        "error_code": _safe_identifier(event.error_code, 50),
        "created_at": created_at.isoformat() if created_at else None,
        "metadata": safe_metadata,
    }
