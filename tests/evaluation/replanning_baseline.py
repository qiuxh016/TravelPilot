"""Deterministic, machine-readable baseline for the current core replan rules."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff, replan

BASELINE_PATH = Path(__file__).parent / "cases" / "replanning_baseline.json"
NAMESPACE = UUID("12345678-1234-5678-1234-567812345678")


def run_baseline() -> dict:
    dataset = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    results = []
    for case in dataset["cases"]:
        nodes = [
            Node(
                uuid5(NAMESPACE, f"{case['id']}:{item['key']}"),
                item["key"],
                item["category"],
                date.fromisoformat(item["day"]),
                time.fromisoformat(item["start"]),
                time.fromisoformat(item["end"]),
                item["priority"],
                item.get("status", "PLANNED"),
                environment_profile=item.get("environment_profile"),
            )
            for item in case["nodes"]
        ]
        payload = dict(case["payload"])
        if target := payload.pop("target_key", None):
            payload["poi_id"] = str(uuid5(NAMESPACE, f"{case['id']}:{target}"))
        updated, warnings = replan(
            nodes,
            case["event_type"],
            datetime.fromisoformat(case["effective_at"]),
            payload,
        )
        diff = plan_diff(nodes, updated, case["event_type"])
        names = {node.name: node for node in updated}
        result = {
            "case_id": case["id"],
            "event_type": case["event_type"],
            "removed": sorted(node.name for node in nodes if node.stable_key not in {n.stable_key for n in updated}),
            "moved": sorted(change["before"]["name"] for change in diff if change["operation"] == "MOVE"),
            "fixed": sorted(name for name, node in names.items() if node.status == "FIXED"),
            "preserved_fixed_or_reserved": sorted(
                node.name for node in updated if node.status in {"FIXED", "RESERVED"}
            ),
            "changed_node_count": sum(change["operation"] != "KEEP" for change in diff),
            "change_cost": sum(change["change_cost"] for change in diff),
            "warnings": warnings,
            "diff": diff,
        }
        for field in ("expected_removed", "expected_moved", "expected_fixed", "expected_preserved"):
            if field in case:
                expected = sorted(case[field])
                actual_field = {
                    "expected_removed": "removed",
                    "expected_moved": "moved",
                    "expected_fixed": "fixed",
                    "expected_preserved": None,
                }[field]
                if actual_field:
                    result[f"{field}_match"] = result[actual_field] == expected
                else:
                    result[f"{field}_match"] = sorted(
                        set(expected) & {node.name for node in updated}
                    ) == expected
        results.append(result)

    return {
        "baseline_id": dataset["baseline_id"],
        "dataset_version": dataset["dataset_version"],
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "scope": "deterministic synthetic weather-aware core replan behavior; not real-world itinerary quality",
        "scenario_count": len(results),
        "all_expectations_match": all(
            value
            for result in results
            for key, value in result.items()
            if key.endswith("_match")
        ),
        "total_changed_nodes": sum(item["changed_node_count"] for item in results),
        "total_change_cost": sum(item["change_cost"] for item in results),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="also write the JSON result to this path")
    args = parser.parse_args()
    output = json.dumps(run_baseline(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
