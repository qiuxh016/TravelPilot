"""Audit whether TravelPilot can fairly run the official TREK task set.

This is a scope audit, not a planner score. It downloads only public task metadata,
pins it to an upstream commit, and applies the declared capability gate below.
It does not read TREK gold outputs, call an LLM, or call TravelPilot services.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


UPSTREAM_REPO = (
    "https://github.com/TonyQJH/"
    "TREK-A-Travel-Reasoning-and-Evaluation-Kit-for-LLM-Agents-in-Complex-Trip-Planning"
)
UPSTREAM_COMMIT = "6ceb4ebb2debd69c5c7c4ba34b5b17524756912b"
TASKS_URL = (
    "https://raw.githubusercontent.com/TonyQJH/"
    "TREK-A-Travel-Reasoning-and-Evaluation-Kit-for-LLM-Agents-in-Complex-Trip-Planning/"
    f"{UPSTREAM_COMMIT}/trek_queries.csv"
)

# Current direct planner contract, inspected in packages/core/.../itinerary.py:
# timed attraction candidates, opening-hour windows and an optional travel-time matrix.
# It does not plan flights, hotels, rental cars, or end-to-end budgets/persona needs.
SUPPORTED_REQUIREMENTS = {"attraction"}


def parse_requirement(value: str) -> dict[str, Any]:
    if not value or value == "{}":
        return {}
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a dictionary requirement, received {type(parsed).__name__}")
    return parsed


def download_tasks(path: Path) -> bytes:
    request = urllib.request.Request(TASKS_URL, headers={"User-Agent": "TravelPilot-TREK-audit"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def load_tasks(path: Path, refresh: bool) -> tuple[list[dict[str, str]], str]:
    if refresh or not path.exists():
        payload = download_tasks(path)
    else:
        payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    rows = list(csv.DictReader(payload.decode("utf-8-sig").splitlines()))
    if len(rows) != 800:
        raise ValueError(f"Expected 800 TREK tasks; found {len(rows)}")
    return rows, digest


def audit(rows: list[dict[str, str]], sha256: str) -> dict[str, Any]:
    requirement_counts: Counter[str] = Counter()
    compatible: list[int] = []
    impossible = Counter()
    levels = Counter()
    malformed: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        requirements = {}
        try:
            for requirement in ("flight", "car", "hotel", "attraction"):
                requirements[requirement] = parse_requirement(row[f"req_{requirement}"])
                if requirements[requirement]:
                    requirement_counts[requirement] += 1
        except (KeyError, ValueError, SyntaxError) as exc:
            malformed.append({"query_index": index, "error": str(exc)})
            continue

        is_impossible = row.get("impossible", "") in {"1", "1.0", "true", "True"}
        impossible["infeasible" if is_impossible else "feasible"] += 1
        levels[row.get("level", "unknown")] += 1

        active = {name for name, value in requirements.items() if value}
        # Strict whole-task gate: no unsupported requirement families, and a non-empty
        # attraction task. Infeasibility labels must also be explainable by supported
        # attraction-only evidence; TREK's typed causes include route/entity/budget.
        if active and active <= SUPPORTED_REQUIREMENTS and not is_impossible:
            compatible.append(index)

    return {
        "audit_type": "compatibility_only",
        "benchmark": "TREK",
        "upstream_repo": UPSTREAM_REPO,
        "upstream_commit": UPSTREAM_COMMIT,
        "tasks_sha256": sha256,
        "task_count": len(rows),
        "feasibility_labels": dict(impossible),
        "level_counts": dict(levels),
        "requirement_task_counts": dict(requirement_counts),
        "directly_compatible_task_indices": compatible,
        "directly_compatible_task_count": len(compatible),
        "compatibility_rule": (
            "Whole-task evaluation requires attraction-only, feasible tasks. Flight, hotel, "
            "car, end-to-end budget, implicit-persona, and typed-infeasibility requirements "
            "are outside the current direct itinerary planner contract."
        ),
        "malformed_rows": malformed,
        "result": "NO_FAIR_WHOLE_TASK_SUBSET" if not compatible else "COMPATIBLE_SUBSET_FOUND",
        "limitations": [
            "This is not a TREK planner score and makes no performance claim.",
            "The audit does not use gold outputs or invoke any model/provider.",
            "TREK data uses a synthetic world and must not be treated as real travel facts.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "data" / "trek_queries.csv")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "compatibility_audit.json")
    parser.add_argument("--refresh", action="store_true", help="redownload metadata from the pinned upstream commit")
    args = parser.parse_args()

    rows, digest = load_tasks(args.data, args.refresh)
    result = audit(rows, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "task_count", "feasibility_labels", "requirement_task_counts",
        "directly_compatible_task_count", "result", "tasks_sha256",
    )}, ensure_ascii=False, indent=2))
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
