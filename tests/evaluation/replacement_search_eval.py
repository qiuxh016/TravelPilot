"""Run the deterministic local replacement-search evaluation batch."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    select_weather_replacement,
)
from travelpilot_core.engines.impact_analysis.replan import Node

CASES_PATH = Path(__file__).parent / "cases" / "replacement_scenarios.json"
NAMESPACE = UUID("9f0b5e0e-0f2b-4f64-9922-c152cd2a11d3")


def run_evaluation() -> dict:
    dataset = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    results = []
    for case in dataset["cases"]:
        def poi_id(key: str, case_id: str = case["id"]) -> UUID:
            return uuid5(NAMESPACE, f"{case_id}:{key}")

        day = date(2026, 10, 1)
        removed_data = case["removed"]
        removed = Node(poi_id(removed_data["key"]), removed_data["key"], "ATTRACTION", day,
                       time.fromisoformat(removed_data["start"]), time.fromisoformat(removed_data["end"]), 3)
        scheduled = [Node(poi_id(item["key"]), item["key"], "ATTRACTION", day,
                          time.fromisoformat(item["start"]), time.fromisoformat(item["end"]), 3)
                     for item in case["scheduled"]]
        candidates = [ReplanCandidate(
            poi_id(item["key"]), item["key"], "MUSEUM", 30.6, 104.0,
            item["duration_min"], item["priority"], item.get("interest_score", 0.5),
            item.get("opening_hours"), item.get("environment_profile"),
        ) for item in case["candidates"]]
        travel_minutes = {
            (poi_id(edge["from"]), poi_id(edge["to"])): edge["minutes"]
            for edge in case["routes"]
        }
        replacement, reason = select_weather_replacement(
            removed, scheduled, candidates, travel_minutes, condition="RAIN",
        )
        result = {
            "case_id": case["id"],
            "selected_candidate": replacement.name if replacement else None,
            "reason": reason,
            "expected_match": (
                (replacement.name if replacement else None) == case["expected_candidate"]
                and reason == case["expected_reason"]
            ),
            "route_cost_min": sum(
                edge["minutes"] for edge in case["routes"]
                if replacement and replacement.name in {edge["from"], edge["to"]}
            ) if replacement else None,
            "scheduled_node_changes": 1 if replacement else 0,
        }
        results.append(result)
    return {
        "evaluation_id": dataset["evaluation_id"],
        "dataset_version": dataset["dataset_version"],
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "scope": "synthetic deterministic replacement feasibility; not real-world provider or itinerary quality",
        "scenario_count": len(results),
        "all_expectations_match": all(item["expected_match"] for item in results),
        "selected_count": sum(item["selected_candidate"] is not None for item in results),
        "rejected_count": sum(item["selected_candidate"] is None for item in results),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = json.dumps(run_evaluation(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
