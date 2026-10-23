"""Small synthetic comparison of fixed, loop-only, and verified route actions.

This evaluates only route-evidence handling. It is not the planned end-to-end
B1/B2/B3 Advisor experiment and does not use an LLM or live map provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

ROOT = Path(__file__).resolve().parents[2]
CASE_FILE = ROOT / "tests/evaluation/cases/advisor_route_evidence_v1.json"
RESULT_FILE = ROOT / "tests/evaluation/results/advisor_route_evidence_v1.json"
POINTS = [(30.1, 104.1), (30.2, 104.2)]
VALID_MATRIX = [[0, 12], [15, 0]]


class MatrixInput(BaseModel):
    points: list[tuple[float, float]]


async def _trace(_entry: dict) -> None:
    return None


async def _execute_case(group: str, case: dict[str, Any]) -> dict[str, Any]:
    async def matrix(_arguments: MatrixInput) -> dict:
        if case["tool_result"] == "failure":
            raise ToolFailure("MAP_UNAVAILABLE")
        durations = VALID_MATRIX if case["tool_result"] == "valid" else [[0, -1], [8]]
        return {"durations_min": durations, "status": "SIMULATED", "source": "fixture"}

    runtime = AgentRuntime(
        [ToolSpec("get_distance_matrix", MatrixInput, matrix, retry_limit=0)],
        _trace,
        max_steps=2,
        max_attempts=2,
    )
    initial: dict[str, Any] = {"trip_context": {"stop_count": len(POINTS)}}
    if case["initial_evidence"] == "fresh_verified":
        initial["get_distance_matrix"] = {
            "durations_min": VALID_MATRIX, "status": "VERIFIED", "source": "fixture",
        }

    if group == "B1_FIXED":
        try:
            output = await runtime.call("get_distance_matrix", {"points": POINTS})
            accepted = verify_distance_matrix(output, len(POINTS))
            status = "VERIFIED" if accepted else "SAFE_FALLBACK"
        except ToolFailure as exc:
            accepted = False
            status = "SAFE_FALLBACK"
            error = exc.code
        else:
            error = None
        expected_match = (
            runtime.tool_attempts == 0 if case["expected"] == "reuse_without_call"
            else accepted if case["expected"] == "query_and_verify"
            else not accepted and status == "SAFE_FALLBACK"
        )
    else:
        async def policy(observation: dict, _allowed: list[str]) -> AgentAction:
            if "get_distance_matrix" in observation:
                return AgentAction(kind="FINISH", summary="matrix already available")
            if "get_distance_matrix_failure" in observation and group == "B3_VERIFIED_RECOVERY":
                return AgentAction(kind="FINISH", summary="safe local fallback")
            return AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={"points": POINTS})

        async def verifier(_tool: str, output: dict) -> bool:
            if group == "B2_LOOP_ONLY":
                return True
            return verify_distance_matrix(output, len(POINTS))

        result = await run_constrained_loop(
            runtime=runtime,
            policy=policy,
            verifier=verifier,
            checkpoint=_noop_checkpoint,
            initial_observation=initial,
            max_decisions=2,
            allowed_tool_names={"get_distance_matrix"},
            continue_on_tool_failure=group == "B3_VERIFIED_RECOVERY",
        )
        accepted = "get_distance_matrix" in result.observation
        status = result.status
        error = result.error
        expected_match = (
            runtime.tool_attempts == 0 and status == "SUCCEEDED"
            if case["expected"] == "reuse_without_call"
            else accepted and status == "SUCCEEDED"
            if case["expected"] == "query_and_verify"
            else not accepted and status == "SUCCEEDED"
            if case["expected"] == "stop_and_fallback_safely"
            else not accepted and status == "REJECTED"
        )

    return {
        "case_id": case["id"], "status": status,
        "expected_action": case["expected"], "expected_match": expected_match,
        "tool_attempts": runtime.tool_attempts, "accepted_matrix": accepted,
        "error_code": error,
    }


async def _noop_checkpoint(_state: dict) -> None:
    return None


async def _run() -> dict:
    dataset = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    encoded_cases = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    results = {}
    for group in ("B1_FIXED", "B2_LOOP_ONLY", "B3_VERIFIED_RECOVERY"):
        runs = [await _execute_case(group, case) for case in dataset["cases"]]
        results[group] = {
            "case_count": len(runs),
            "expected_action_matches": sum(item["expected_match"] for item in runs),
            "match_rate": sum(item["expected_match"] for item in runs) / len(runs),
            "accepted_invalid_matrices": sum(
                item["accepted_matrix"] for item in runs if item["case_id"] == "malformed-matrix"
            ),
            "runs": runs,
        }
    return {
        "evaluation_id": dataset["dataset_id"],
        "provider_mode": "SYNTHETIC_DETERMINISTIC",
        "case_file_sha256": hashlib.sha256(encoded_cases).hexdigest(),
        "run_at_utc": datetime.now(UTC).isoformat(),
        "scope": dataset["scope"],
        "groups": results,
        "interpretation": "A four-case component check only; not end-to-end Advisor quality or a live-model result.",
    }


def main() -> None:
    result = asyncio.run(_run())
    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        group: {
            "expected_action_matches": summary["expected_action_matches"],
            "case_count": summary["case_count"],
            "accepted_invalid_matrices": summary["accepted_invalid_matrices"],
        }
        for group, summary in result["groups"].items()
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
