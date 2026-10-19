"""Compare the former category heuristic with current evidence-aware rules."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replan import Node, affected, plan_diff, replan

CASES_PATH = Path(__file__).parent / "cases" / "replanning_baseline.json"
NAMESPACE = UUID("12345678-1234-5678-1234-567812345678")


def run_comparison() -> dict:
    dataset = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    results = []
    for case in dataset["cases"]:
        if case["event_type"] != "WEATHER_CHANGE":
            continue
        event_at = datetime.fromisoformat(case["effective_at"])
        nodes = [Node(
            uuid5(NAMESPACE, f"{case['id']}:{item['key']}"), item["key"], item["category"],
            datetime.fromisoformat(item["day"]).date(), time.fromisoformat(item["start"]),
            time.fromisoformat(item["end"]), item["priority"], item.get("status", "PLANNED"),
            environment_profile=item.get("environment_profile"),
        ) for item in case["nodes"]]
        legacy_removed = sorted(
            node.name for node in nodes
            if affected(node, event_at) and node.status not in {"FIXED", "RESERVED"}
            and node.category == "ATTRACTION"
        )
        current, _warnings = replan(nodes, case["event_type"], event_at, dict(case["payload"]))
        current_diff = plan_diff(nodes, current, case["event_type"])
        current_removed = sorted(
            change["before"]["name"] for change in current_diff if change["operation"] == "REMOVE"
        )
        false_removals = sorted(set(legacy_removed) - set(current_removed))
        results.append({
            "case_id": case["id"],
            "legacy_category_heuristic_removed": legacy_removed,
            "evidence_aware_removed": current_removed,
            "avoided_unverified_removals": false_removals,
            "legacy_change_cost": 3 * len(legacy_removed),
            "evidence_aware_change_cost": sum(change["change_cost"] for change in current_diff),
        })
    return {
        "comparison_id": "weather_replanning_category_vs_evidence_v2",
        "dataset_id": dataset["baseline_id"],
        "dataset_version": dataset["dataset_version"],
        "scenario_count": len(results),
        "avoided_unverified_removal_count": sum(len(item["avoided_unverified_removals"]) for item in results),
        "scope": "same synthetic cases; old behavior is a faithful category-only ablation, not an external baseline",
        "results": results,
    }
