"""Measure deterministic preference-query alignment on frozen synthetic cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from travelpilot_api.advisor.interpretation import (
    _destination_queries,
    _prioritized_destination_queries,
)

from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_preference_relevance_v2.json"
RESULT = ROOT / "tests/evaluation/results/advisor_preference_relevance_v2.json"


def evaluate_cases(
    cases: list[dict[str, Any]], *, dataset_id: str = "advisor_preference_relevance_v2",
) -> dict[str, Any]:
    rows = []
    for case in cases:
        with_memory = [
            label for label, _query in _prioritized_destination_queries(
                case["current_request"],
                case["confirmed_preferences"],
                case["hard_constraints"],
            )
        ]
        without_memory = [
            label for label, _query in _destination_queries(
                case["current_request"], case["hard_constraints"],
            )
        ]
        expected_on = set(case["expected_with_memory"])
        expected_off = set(case["expected_without_memory"])
        forbidden = set(case["forbidden_with_memory"])
        rows.append({
            "id": case["id"],
            "with_memory": with_memory,
            "without_memory": without_memory,
            "expected_with_memory": sorted(expected_on),
            "expected_without_memory": sorted(expected_off),
            "unexpected_memory_interests": sorted(set(with_memory) & forbidden),
            "memory_alignment_passed": set(with_memory) == expected_on,
            "baseline_alignment_passed": set(without_memory) == expected_off,
        })
    return {
        "evaluation_id": dataset_id,
        "provider_mode": "DETERMINISTIC_QUERY_POLICY",
        "source_provenance": evaluation_provenance(dataset_id),
        "case_count": len(rows),
        "memory_alignment_passed": sum(row["memory_alignment_passed"] for row in rows),
        "baseline_alignment_passed": sum(row["baseline_alignment_passed"] for row in rows),
        "explicit_scope_violations": sum(bool(row["unexpected_memory_interests"]) for row in rows),
        "results": rows,
        "claim_boundary": (
            "Measures deterministic query alignment against authored current-intent labels only; "
            "not user satisfaction, LLM quality, real POI relevance, or travel outcomes."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RESULT)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_cases(dataset["cases"], dataset_id=dataset["dataset_id"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_provenance"}, ensure_ascii=False))
    return 0 if (
        report["memory_alignment_passed"] == report["case_count"]
        and report["baseline_alignment_passed"] == report["case_count"]
        and report["explicit_scope_violations"] == 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
