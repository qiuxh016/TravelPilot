"""Aggregates measured outcomes from a fixed evaluation batch."""

from collections.abc import Iterable


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def summarize(records: Iterable[dict]) -> dict:
    records = list(records)
    total = len(records)
    changes = [record.get("changed_nodes", 0) for record in records]
    costs = [record.get("change_cost", 0) for record in records]
    latencies = sorted(max(0, int(record.get("latency_ms", 0))) for record in records)
    p95_latency_ms = latencies[max(0, int(len(latencies) * 0.95 + 0.999999) - 1)] if latencies else 0
    return {
        "plan_success_rate": ratio(sum(record.get("plan_success", False) for record in records), total),
        "constraint_violation_rate": ratio(sum(record.get("constraint_violations", 0) for record in records), total),
        "opening_hour_conflict_rate": ratio(sum(record.get("opening_conflicts", 0) for record in records), total),
        "replanning_success_rate": ratio(sum(record.get("replan_success", False) for record in records),
                                          sum(record.get("is_replan", False) for record in records)),
        "tool_success_rate": ratio(sum(record.get("tool_successes", 0) for record in records),
                                   sum(record.get("tool_calls", 0) for record in records)),
        "changed_node_count": sum(changes),
        "change_cost": sum(costs),
        "latency_ms": sum(latencies),
        "p95_latency_ms": p95_latency_ms,
        "token_cost": sum(record.get("token_cost", 0) for record in records),
    }
