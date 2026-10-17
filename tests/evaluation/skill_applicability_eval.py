"""Evaluate deterministic Skill selection and rejection reasons by context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from travelpilot_core.skills import select_skill_decision

ROOT = Path(__file__).resolve().parents[2]


def run_evaluation() -> dict[str, Any]:
    all_cases = json.loads(
        (ROOT / "tests/evaluation/cases/scenarios.json").read_text(encoding="utf-8")
    )
    cases = [case for case in all_cases if case["kind"] == "skill"]
    results = []
    for case in cases:
        context = {"task": case["task"], **case["context"]}
        decision = select_skill_decision(case["task"], context=context)
        selected = decision.skill is not None
        reason = decision.applicability.reason_code
        expected_match = selected is case["expect_selected"]
        if not case["expect_selected"]:
            expected_match = expected_match and reason == case["expect_reason"]
        elif "expect_skill" in case:
            expected_match = expected_match and decision.skill.name == case["expect_skill"]
        results.append({
            "case_id": case["id"],
            "selected": selected,
            "skill_name": decision.skill.name if decision.skill else None,
            "skill_version": decision.skill.version if decision.skill else None,
            "reason_code": reason,
            "missing_requirements": list(decision.applicability.missing_requirements),
            "matched_exclusions": list(decision.applicability.matched_exclusions),
            "expected_match": expected_match,
        })
    return {
        "evaluation_id": "skill_applicability_v1",
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "scenario_count": len(results),
        "all_expectations_match": all(row["expected_match"] for row in results),
        "scope": "Skill routing and reason codes only; does not evaluate LLM output quality",
        "results": results,
    }
