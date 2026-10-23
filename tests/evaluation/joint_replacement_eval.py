"""Compare bounded joint replacement assignment with sequential greedy selection."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, time
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    search_weather_replacements,
    select_weather_replacement,
)
from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff

CASES_PATH = Path(__file__).parent / "cases" / "joint_replacement_scenarios.json"
NAMESPACE = UUID("594e8291-307d-4d6e-9223-93cad7bbce22")


def _poi_id(case_id: str, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{case_id}:{key}")


def _node(case_id: str, item: dict, *, exposure: str = "OUTDOOR") -> Node:
    return Node(
        _poi_id(case_id, item["key"]),
        item["key"],
        "ATTRACTION",
        date.fromisoformat(item.get("day", "2026-10-01")),
        time.fromisoformat(item["start"]),
        time.fromisoformat(item["end"]),
        item.get("priority", 3),
        environment_profile={
            "exposure": exposure,
            "sensitivity": {"RAIN": "HIGH" if exposure == "OUTDOOR" else "LOW"},
            "source": "EDITORIAL",
            "confidence": 0.9,
        },
    )


def _route_cost(nodes: list[Node], replacement_ids: set[UUID], travel: dict) -> int | None:
    edges = []
    days = sorted({node.day for node in nodes})
    for day in days:
        ordered = sorted(
            (node for node in nodes if node.day == day),
            key=lambda node: (node.start_time, node.stable_key),
        )
        edges.extend(
            (left.poi_id, right.poi_id)
            for left, right in pairwise(ordered)
            if left.poi_id in replacement_ids or right.poi_id in replacement_ids
        )
    values = [travel.get(edge) for edge in edges]
    return sum(values) if all(value is not None for value in values) else None


def run_evaluation() -> dict:
    dataset = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    results = []
    for case in dataset["cases"]:
        case_id = case["id"]
        removed = [_node(case_id, item) for item in case["removed"]]
        scheduled = [_node(case_id, item, exposure="INDOOR") for item in case["scheduled"]]
        candidate_specs = [
            {"key": item} if isinstance(item, str) else item
            for item in case["candidates"]
        ]
        candidates = [
            ReplanCandidate(
                _poi_id(case_id, item["key"]), item["key"].upper(), "MUSEUM", 30.6, 104.0,
                45, item.get("priority", 3), item.get("interest_score", 0.5),
                {"open": "09:00", "close": "20:00"},
                {"exposure": "INDOOR", "sensitivity": {"RAIN": "LOW"},
                 "source": "EDITORIAL", "confidence": 0.9},
            )
            for item in candidate_specs
        ]
        travel = {
            (_poi_id(case_id, edge["from"]), _poi_id(case_id, edge["to"])): edge["minutes"]
            for edge in case["routes"]
        }

        greedy_nodes = list(scheduled)
        greedy_candidates = list(candidates)
        greedy_assignment = []
        for lost in sorted(removed, key=lambda node: (node.day, node.start_time)):
            replacement, _ = select_weather_replacement(
                lost, greedy_nodes, greedy_candidates, travel, condition="RAIN",
            )
            greedy_assignment.append(replacement.name.lower() if replacement else None)
            if replacement:
                greedy_nodes.append(replacement)
                greedy_candidates = [
                    candidate for candidate in greedy_candidates
                    if candidate.poi_id != replacement.poi_id
                ]

        joint_search = search_weather_replacements(
            removed, scheduled, candidates, travel, condition="RAIN",
        )
        joint = joint_search.assignments
        narrow_search = search_weather_replacements(
            removed, scheduled, candidates, travel, condition="RAIN", beam_width=1,
        )
        narrow = narrow_search.assignments
        joint_nodes = [*scheduled, *(replacement for _, replacement, _ in joint if replacement)]
        narrow_nodes = [*scheduled, *(replacement for _, replacement, _ in narrow if replacement)]
        joint_assignment = [replacement.name.lower() if replacement else None for _, replacement, _ in joint]
        joint_reasons = [reason for _, _, reason in joint]
        narrow_assignment = [replacement.name.lower() if replacement else None for _, replacement, _ in narrow]
        original_plan = [*removed, *scheduled]
        greedy_diff = plan_diff(original_plan, greedy_nodes, "WEATHER_CHANGE")
        joint_diff = plan_diff(original_plan, joint_nodes, "WEATHER_CHANGE")
        narrow_diff = plan_diff(original_plan, narrow_nodes, "WEATHER_CHANGE")
        greedy_ids = {node.poi_id for node in greedy_nodes if node.note and node.note.startswith("WEATHER_REPLACEMENT:")}
        joint_ids = {node.poi_id for node in joint_nodes if node.note and node.note.startswith("WEATHER_REPLACEMENT:")}
        narrow_ids = {node.poi_id for node in narrow_nodes if node.note and node.note.startswith("WEATHER_REPLACEMENT:")}
        greedy_route_cost = _route_cost(greedy_nodes, greedy_ids, travel)
        joint_route_cost = _route_cost(joint_nodes, joint_ids, travel)
        narrow_route_cost = _route_cost(narrow_nodes, narrow_ids, travel)
        expected_match = (
            joint_assignment == case["expected_joint_assignment"]
            and greedy_assignment == case["expected_greedy_assignment"]
            and sum(replacement is not None for replacement in joint_assignment)
            == case.get("expected_selected_count", len(removed))
        )
        if "expected_joint_reason" in case:
            expected_match = expected_match and joint_reasons == [case["expected_joint_reason"]]
        if "expected_joint_reasons" in case:
            expected_match = expected_match and joint_reasons == case["expected_joint_reasons"]
        if "expected_narrow_beam_assignment" in case:
            expected_match = (
                expected_match
                and narrow_assignment == case["expected_narrow_beam_assignment"]
                and narrow_route_cost == case["expected_narrow_beam_route_cost_min"]
                and narrow_search.states_pruned > 0
                and joint_route_cost < narrow_route_cost
                and sum(item["change_cost"] for item in joint_diff)
                == sum(item["change_cost"] for item in greedy_diff)
                == sum(item["change_cost"] for item in narrow_diff)
            )
        results.append({
            "case_id": case_id,
            "greedy_assignment": greedy_assignment,
            "joint_assignment": joint_assignment,
            "joint_reasons": joint_reasons,
            "narrow_beam_assignment": narrow_assignment,
            "greedy_route_cost_min": greedy_route_cost,
            "joint_route_cost_min": joint_route_cost,
            "narrow_beam_route_cost_min": narrow_route_cost,
            "joint_objective": joint_search.objective,
            "joint_objective_weights": joint_search.objective_weights,
            "joint_beam_width": joint_search.beam_width,
            "joint_states_pruned": joint_search.states_pruned,
            "joint_peak_frontier_size": joint_search.peak_frontier_size,
            "narrow_beam_states_pruned": narrow_search.states_pruned,
            "greedy_plan_change_cost": sum(item["change_cost"] for item in greedy_diff),
            "joint_plan_change_cost": sum(item["change_cost"] for item in joint_diff),
            "expected_match": expected_match,
        })

    return {
        "evaluation_id": dataset["evaluation_id"],
        "dataset_version": dataset["dataset_version"],
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "scope": "synthetic assignment trap; route costs and change costs do not establish real-world itinerary quality",
        "scenario_count": len(results),
        "all_expectations_match": all(item["expected_match"] for item in results),
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
