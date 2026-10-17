"""Evaluate deterministic feasibility and diagnostics for USER_ADD insertion."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from travelpilot_core.engines.impact_analysis.insertion import (
    UserAddCandidate,
    find_user_add_slot,
)
from travelpilot_core.engines.impact_analysis.replan import Node

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests/evaluation/cases/user_add_scenarios.json"


def run_evaluation() -> dict:
    data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    results = []
    for case in data["cases"]:
        node_ids = {
            item["id"]: uuid5(NAMESPACE_URL, f"user-add:{case['id']}:{item['id']}")
            for item in case["existing"]
        }
        candidate_id = uuid5(NAMESPACE_URL, f"user-add:{case['id']}:candidate")
        existing = [
            Node(
                node_ids[item["id"]],
                item["id"],
                "ATTRACTION",
                date_from(case["effective_at"]),
                time.fromisoformat(item["start"]),
                time.fromisoformat(item["end"]),
                5,
                item.get("status", "PLANNED"),
            )
            for item in case["existing"]
        ]
        existing_before = tuple(existing)
        route_minutes = {}
        route_ids = {**node_ids, "candidate": candidate_id}
        for edge, minutes in case["routes"].items():
            origin, destination = edge.split("->", 1)
            route_minutes[(route_ids[origin], route_ids[destination])] = minutes
        candidate, reason = find_user_add_slot(
            existing,
            UserAddCandidate(
                candidate_id,
                "候选地点",
                "ATTRACTION",
                case["candidate"]["duration_min"],
                5,
                {"open": case["candidate"]["open"], "close": case["candidate"]["close"]},
            ),
            datetime.fromisoformat(case["effective_at"]),
            route_minutes,
        )
        expected = case["expected"]
        actual = {
            "reason_code": reason,
            "start": candidate.start_time.strftime("%H:%M") if candidate else None,
            "fixed_preserved": all(
                tuple(node for node in existing if node.status == "FIXED")
                == tuple(node for node in existing_before if node.status == "FIXED")
                for item in case["existing"]
                if item.get("status") == "FIXED"
            ),
        }
        results.append({
            "case_id": case["id"],
            "actual": actual,
            "expected": expected,
            "expected_match": all(actual.get(key) == value for key, value in expected.items()),
        })
    return {
        "evaluation_id": data["evaluation_id"],
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "case_count": len(results),
        "all_expectations_match": all(item["expected_match"] for item in results),
        "scope": data["scope"],
        "results": results,
    }


def date_from(value: str):
    return datetime.fromisoformat(value).date()
