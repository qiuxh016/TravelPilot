"""Deterministic probes for profile-memory transfer boundaries.

This harness tests the existing server policy, not user utility or model quality.
"""

import json
from pathlib import Path
from typing import Any

from travelpilot_api.advisor.policies import (
    classify_draft_memory_conflicts,
    overridden_profile_memory_texts,
)

CASES_PATH = Path(__file__).parent / "cases" / "advisor_memory_transfer_v1.json"


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for case in cases:
        profile, trip = classify_draft_memory_conflicts(
            [case["candidate"]], case["profile_memories"], case["trip_constraints"],
        )
        overridden = overridden_profile_memory_texts(
            [case["candidate"]], case["profile_memories"],
            explicitly_overridden=case["explicit_override"],
        )
        actual = {
            "profile_conflicts": len(profile),
            "trip_conflicts": len(trip),
            "overridden_memories": len(overridden),
        }
        expected = {
            "profile_conflicts": case["expect_profile_conflicts"],
            "trip_conflicts": case["expect_trip_conflicts"],
            "overridden_memories": case["expect_overridden_memories"],
        }
        results.append({
            "id": case["id"], "actual": actual, "expected": expected,
            "passed": actual == expected,
        })
    return {
        "schema_version": 1,
        "evaluator": "deterministic_server_policy",
        "case_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "results": results,
        "claim_boundary": "Synthetic contract coverage only; not a user-utility or LLM-quality measurement.",
    }


def main() -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    report = evaluate_cases(cases)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
