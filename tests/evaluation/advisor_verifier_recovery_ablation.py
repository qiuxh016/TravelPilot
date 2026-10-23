"""Deterministic production-loop ablation for verifier and recovery behavior.

This isolates failure-handling mechanics from model decision quality. Actions
and tool outputs are scripted fixtures; the constrained loop and verifier are
the production implementations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "tests/evaluation/results/advisor_verifier_recovery_ablation_local.json"
ARMS = ("B2_LOOP_WITHOUT_EVIDENCE_VERIFIER", "B3_LOOP_WITH_VERIFIER_AND_RECOVERY")
SCENARIOS = {
    "valid_matrix": {"expected_size": 2, "fault": None},
    "wrong_matrix_size": {"expected_size": 3, "fault": "wrong_size"},
    "matrix_provider_timeout": {"expected_size": 2, "fault": "timeout"},
}


class _NoArguments(BaseModel):
    pass


def _matrix(size: int) -> dict[str, Any]:
    return {
        "durations_min": [
            [0 if row == column else 12 for column in range(size)]
            for row in range(size)
        ],
        "source": "LOCAL_FIXTURE",
    }


async def _run_arm(scenario_id: str, arm: str) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_id]
    expected_size = scenario["expected_size"]
    fixture_size = expected_size - 1 if scenario["fault"] == "wrong_size" else expected_size
    calls: list[str] = []
    checkpoints: list[dict[str, Any]] = []

    async def matrix_handler(_arguments: BaseModel) -> dict[str, Any]:
        calls.append("get_distance_matrix")
        if scenario["fault"] == "timeout":
            raise ToolFailure("MAP_PROVIDER_TIMEOUT")
        return _matrix(fixture_size)

    async def draft_handler(_arguments: BaseModel) -> dict[str, Any]:
        calls.append("build_schedule_draft")
        return {"status": "DRAFT_FIXTURE", "formal_write": False}

    async def trace(_event: dict[str, Any]) -> None:
        return None

    async def checkpoint(event: dict[str, Any]) -> None:
        checkpoints.append(event)

    async def policy(observation: dict[str, Any], _allowed: list[str]) -> AgentAction:
        if "get_distance_matrix_verification_failure" in observation:
            return AgentAction(kind="FINISH", summary="路线矩阵未通过验证，停止生成草案。")
        if "get_distance_matrix_failure" in observation:
            return AgentAction(kind="FINISH", summary="路线服务不可用，未生成草案。")
        if "get_distance_matrix" not in observation:
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix")
        if "build_schedule_draft" not in observation:
            return AgentAction(kind="CALL_TOOL", tool="build_schedule_draft")
        return AgentAction(kind="FINISH", summary="待确认草案已生成。")

    async def verifier(tool: str, output: dict[str, Any]) -> bool:
        if arm == "B2_LOOP_WITHOUT_EVIDENCE_VERIFIER":
            return True
        if tool == "get_distance_matrix":
            return verify_distance_matrix(output, expected_size)
        return output.get("status") == "DRAFT_FIXTURE" and output.get("formal_write") is False

    runtime = AgentRuntime(
        [
            ToolSpec("get_distance_matrix", _NoArguments, matrix_handler, retry_limit=0),
            ToolSpec("build_schedule_draft", _NoArguments, draft_handler, retry_limit=0),
        ],
        trace,
        max_steps=2,
        max_attempts=2,
        token_budget=0,
    )
    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=checkpoint,
        initial_observation={"scenario_id": scenario_id},
        max_decisions=3,
        allowed_tool_names={"get_distance_matrix", "build_schedule_draft"},
        continue_on_tool_failure=arm == "B3_LOOP_WITH_VERIFIER_AND_RECOVERY",
        continue_on_verifier_failure=arm == "B3_LOOP_WITH_VERIFIER_AND_RECOVERY",
    )
    draft_calls = calls.count("build_schedule_draft")
    if draft_calls and scenario["fault"] == "wrong_size":
        task_outcome = "DRAFT_WITH_UNVERIFIED_ROUTE"
    elif draft_calls:
        task_outcome = "DRAFT_PROPOSED"
    elif scenario["fault"] == "timeout" and arm == "B3_LOOP_WITH_VERIFIER_AND_RECOVERY":
        task_outcome = "SAFE_STOP_UNVERIFIED"
    else:
        task_outcome = "FAILED"
    return {
        "scenario_id": scenario_id,
        "arm": arm,
        "loop_status": result.status,
        "task_outcome": task_outcome,
        "terminal_error": result.error,
        "tool_calls": calls,
        "draft_calls": draft_calls,
        "invalid_matrix_accepted": (
            scenario["fault"] == "wrong_size"
            and "build_schedule_draft" in calls
        ) if arm == "B2_LOOP_WITHOUT_EVIDENCE_VERIFIER" else False,
        "checkpoints": checkpoints,
    }


async def evaluate() -> dict[str, Any]:
    rows = [
        await _run_arm(scenario_id, arm)
        for scenario_id in SCENARIOS
        for arm in ARMS
    ]
    grouped = {
        scenario_id: {
            row["arm"]: row for row in rows if row["scenario_id"] == scenario_id
        }
        for scenario_id in SCENARIOS
    }
    malformed = grouped["wrong_matrix_size"]
    outage = grouped["matrix_provider_timeout"]
    valid = grouped["valid_matrix"]
    summary = {
        "scenario_count": len(SCENARIOS),
        "arm_scenario_count": len(rows),
        "valid_control_draft_calls": {
            arm: valid[arm]["draft_calls"] for arm in ARMS
        },
        "malformed_matrix_draft_calls": {
            arm: malformed[arm]["draft_calls"] for arm in ARMS
        },
        "malformed_matrix_accepted_by_b2": malformed[ARMS[0]]["invalid_matrix_accepted"],
        "malformed_matrix_rejected_by_b3": any(
            item.get("state") == "REJECTED" for item in malformed[ARMS[1]]["checkpoints"]
        ),
        "provider_outage_b2_terminal": outage[ARMS[0]]["loop_status"],
        "provider_outage_b3_terminal": outage[ARMS[1]]["loop_status"],
        "provider_outage_task_outcomes": {
            arm: outage[arm]["task_outcome"] for arm in ARMS
        },
        "provider_outage_b3_repeated_failed_call": (
            outage[ARMS[1]]["tool_calls"].count("get_distance_matrix") > 1
        ),
    }
    return {
        "evaluation_id": "advisor_verifier_recovery_ablation_local",
        "provider_mode": "SCRIPTED_POLICY_AND_SYNTHETIC_TOOL_OUTPUTS",
        "source_provenance": evaluation_provenance("advisor_verifier_recovery_ablation"),
        "scenario_ids": list(SCENARIOS),
        "arms": list(ARMS),
        "summary": summary,
        "runs": rows,
        "scope": (
            "The production constrained loop and verify_distance_matrix are executed with local fixtures. "
            "B2 deliberately omits evidence verification; B3 enables verifier rejection and bounded recovery. "
            "A SUCCEEDED loop status on provider failure means it ended safely, not that itinerary work completed. "
            "This measures deterministic safety mechanics, not model decisions, answer quality, or user utility."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
