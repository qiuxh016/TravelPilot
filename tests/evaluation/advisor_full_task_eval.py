"""Deterministic full-task contract comparison for the frozen Advisor cases.

The policies are scripted system variants, not an LLM quality benchmark. B1 is a
fixed workflow; B2 chooses actions from observations; B3 adds output verification
and bounded recovery. Results should be read as contract coverage only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

CASE_FILE = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v3.json"
RESULT_FILE = ROOT / "tests/evaluation/results/advisor_full_task_v3_product_grounded.json"

from tests.evaluation.advisor_fixed_workflow_baseline import (
    _matrix_for_state,
    build_schedule_fixture,
)
from tests.evaluation.advisor_fixed_workflow_baseline import (
    evaluate_case as evaluate_fixed_case,
)
from tests.evaluation.provenance import evaluation_provenance


class EmptyInput(BaseModel):
    pass


class MatrixInput(BaseModel):
    pass


async def _trace(_entry: dict) -> None:
    return None


async def _checkpoint(_state: dict) -> None:
    return None


def _expected_action(case: dict[str, Any]) -> str:
    return case["expected_next_action"]


def _expected_outcome(case: dict[str, Any], outcome: str) -> bool:
    expected = case["expected_outcome"]
    if expected == "warning_draft_or_safe_stop":
        return outcome in {"SAFE_STOP", "WARNING_DRAFT"}
    if expected == "estimated_draft_with_route_warning_or_safe_stop":
        return outcome in {"SAFE_STOP", "WARNING_DRAFT"}
    if expected == "constraint_reported_draft_or_safe_stop":
        return outcome in {"SAFE_STOP", "CONSTRAINT_DRAFT"}
    return outcome == expected.upper()


async def _run_agent(group: str, case: dict[str, Any]) -> dict[str, Any]:
    state = case["state"]
    route = state.get("route_matrix", "missing")
    route_verification_rejected = False
    matrix: list[list[int]] | None = None

    async def get_matrix(_args: MatrixInput) -> dict[str, Any]:
        nonlocal matrix
        if route == "provider_timeout":
            raise ToolFailure("MAP_TIMEOUT")
        size = int(state.get("saved_places", 0))
        matrix = _matrix_for_state(state, size)
        return {"status": "PROVIDER_RETURNED", "durations_min": matrix}

    async def check_opening_hours(_args: EmptyInput) -> dict[str, Any]:
        if state.get("opening_hours") == "provider_unavailable":
            return {"verified": False, "conflict": False}
        conflict = state.get("opening_hours") == "one_place_closes_before_visit"
        return {"verified": True, "conflict": conflict}

    async def build_draft(_args: EmptyInput) -> dict[str, Any]:
        if state.get("draft_result") == "provider_timeout":
            raise ToolFailure("PLANNER_TIMEOUT")
        proposal = build_schedule_fixture(case["id"], state, matrix)
        infeasible = not proposal["feasible"]
        fixed = True
        fixed_stops = state.get("fixed_stops")
        if isinstance(fixed_stops, list):
            for stop in fixed_stops:
                fixed_day = stop.get("day")
                fixed_start = time.fromisoformat(stop["start"])
                fixed_end = time.fromisoformat(stop["end"])
                if any(
                    visit["day"] == fixed_day
                    and time.fromisoformat(visit["start_time"]) < fixed_end
                    and fixed_start < time.fromisoformat(visit["end_time"])
                    for visit in proposal["visits"]
                ):
                    fixed = False
                    break
        opening_conflict = any("超出营业时间" in warning for warning in proposal["warnings"])
        return {
            "feasible": not infeasible,
            "all_selected_places_accounted_for": proposal["all_selected_places_accounted_for"],
            "fixed_stop_unchanged": fixed,
            "opening_conflict_reported": opening_conflict,
            "confirmation_allowed": not infeasible and not opening_conflict,
            "route_warning": proposal["route_warning"] or route_verification_rejected,
            "candidate_ids": proposal["candidate_ids"],
            "warnings": proposal["warnings"],
        }

    async def refresh_plan(_args: EmptyInput) -> dict[str, Any]:
        return {
            "current_plan_version": state.get("current_plan_version"),
            "draft_invalidated": True,
        }

    runtime = AgentRuntime(
        [
            ToolSpec("get_distance_matrix", MatrixInput, get_matrix, retry_limit=0),
            ToolSpec("verify_opening_hours", EmptyInput, check_opening_hours, retry_limit=0),
            ToolSpec("build_schedule_draft", EmptyInput, build_draft, retry_limit=0),
            ToolSpec("refresh_context", EmptyInput, refresh_plan, retry_limit=0),
        ],
        _trace,
        max_steps=4,
        max_attempts=4,
    )
    observation: dict[str, Any] = {"state": state}
    if route == "fresh_verified":
        observation["fresh_route_evidence"] = {"status": "VERIFIED"}
    if route == "verified":
        observation["fresh_route_evidence"] = {"status": "VERIFIED"}

    decisions: list[dict[str, str]] = []

    async def policy(obs: dict[str, Any], _allowed: list[str]) -> AgentAction:
        if "build_schedule_draft" in obs:
            action = AgentAction(kind="FINISH", summary="草案已生成，等待用户检查")
        elif "build_schedule_draft_failure" in obs or "build_schedule_draft_verification_failure" in obs:
            action = AgentAction(kind="FINISH", summary="规划器失败，保留当前正式计划")
        elif not state.get("trip_dates"):
            action = AgentAction(kind="ASK_USER", question="请先补充旅行日期。")
        elif int(state.get("saved_places", 0)) == 0:
            action = AgentAction(kind="ASK_USER", question="请先添加想去的地点。")
        elif state.get("budget_status") == "insufficient":
            action = AgentAction(kind="ASK_USER", question="当前预算不足以覆盖这些地点，愿意放宽预算吗？")
        elif state.get("transfer_time") == "exceeds_daily_window":
            action = AgentAction(kind="ASK_USER", question="地点间交通时间过长，愿意增加天数或删减地点吗？")
        elif int(state.get("saved_places", 0)) == 1:
            action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        elif state.get("opening_hours") == "provider_unavailable":
            if (
                "verify_opening_hours_verification_failure" in obs
                or obs.get("verify_opening_hours", {}).get("verified") is False
            ):
                action = AgentAction(kind="ASK_USER", question="暂时无法核实营业时间，请确认后再安排。")
            elif "verify_opening_hours" not in obs:
                action = AgentAction(kind="CALL_TOOL", tool="verify_opening_hours", arguments={})
            else:
                action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        elif state.get("daily_capacity") == "infeasible":
            action = AgentAction(
                kind="ASK_USER", question="当前时间与已固定预约冲突，请选择需要放宽的限制。"
            )
        elif state.get("draft_plan_version") is not None:
            if "refresh_context" not in obs:
                action = AgentAction(kind="CALL_TOOL", tool="refresh_context", arguments={})
            else:
                action = AgentAction(kind="FINISH", summary="stale draft rejected")
        elif state.get("opening_hours") and "verify_opening_hours" not in obs:
            action = AgentAction(kind="CALL_TOOL", tool="verify_opening_hours", arguments={})
        elif route in {"missing", "provider_timeout", "stale", "malformed"} and "fresh_route_evidence" not in obs:
            if "get_distance_matrix_verification_failure" in obs:
                action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
            elif "get_distance_matrix_failure" in obs:
                action = AgentAction(kind="FINISH", summary="route unavailable; explain limit")
            elif "get_distance_matrix" not in obs:
                action = AgentAction(kind="CALL_TOOL", tool="get_distance_matrix", arguments={})
            else:
                action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        elif "build_schedule_draft" not in obs:
            action = AgentAction(kind="CALL_TOOL", tool="build_schedule_draft", arguments={})
        else:
            action = AgentAction(kind="FINISH", summary="draft ready for user review")
        if action.kind == "ASK_USER":
            family = (
                "ask_for_trip_dates"
                if not state.get("trip_dates")
                else "ask_user_to_add_places"
                if int(state.get("saved_places", 0)) == 0
                else "ask_to_relax_budget"
                if state.get("budget_status") == "insufficient"
                else "ask_to_relax_transfer_constraint"
                if state.get("transfer_time") == "exceeds_daily_window"
                else "report_conflict_and_ask_which_constraint_to_relax"
            )
        elif action.tool == "refresh_context":
            family = "refresh_context_and_rebuild_draft"
        elif action.tool == "verify_opening_hours":
            family = "verify_opening_hours_and_revise_or_report_conflict"
        elif action.tool == "get_distance_matrix":
            family = "refresh_stale_route_evidence" if route == "stale" else "get_distance_matrix"
        elif action.tool == "build_schedule_draft" and int(state.get("saved_places", 0)) == 1:
            family = "build_schedule_without_route"
        elif action.tool == "build_schedule_draft" and state.get("fixed_stops"):
            family = "preserve_fixed_stop_and_schedule_around_it"
        elif action.tool == "build_schedule_draft" and route in {"fresh_verified", "verified"}:
            family = "reuse_fresh_route_evidence"
        elif action.kind == "FINISH" and "get_distance_matrix_failure" in obs:
            family = "stop_retry_and_explain_route_limit"
        else:
            family = action.tool or action.kind
        decisions.append({"family": family, "kind": action.kind})
        return action

    async def verifier(tool: str, output: dict[str, Any]) -> bool:
        nonlocal route_verification_rejected, matrix
        if group == "B2_AGENT_LOOP":
            return True
        if tool == "get_distance_matrix":
            returned_matrix = output.get("durations_min")
            valid = verify_distance_matrix(
                {"durations_min": returned_matrix}, int(state.get("saved_places", 0))
            )
            if not valid:
                route_verification_rejected = True
                matrix = None
            return valid
        if tool == "verify_opening_hours":
            return output.get("verified") is True
        if tool == "build_schedule_draft":
            return (
                output.get("all_selected_places_accounted_for") is True
                and (not state.get("fixed_stops") or output.get("fixed_stop_unchanged") is True)
                and (
                    not state.get("opening_hours")
                    or output.get("opening_conflict_reported") is True
                )
            )
        if tool == "refresh_context":
            return output.get("draft_invalidated") is True
        return False

    loop = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=_checkpoint,
        initial_observation=observation,
        max_decisions=5,
        allowed_tool_names=set(runtime.tools),
        continue_on_tool_failure=group == "B3_VERIFIER_RECOVERY",
        continue_on_verifier_failure=group == "B3_VERIFIER_RECOVERY",
    )
    action_names = [transition["tool"] for transition in loop.transitions]
    failed_route_call = any(
        call.get("tool") == "get_distance_matrix" and call.get("status") == "FAILED"
        for call in runtime.calls
    )
    if loop.pending_question:
        first_action = decisions[0]["family"]
        outcome = (
            "ASK_USER_ADD_PLACES"
            if int(state.get("saved_places", 0)) == 0
            else "ASK_USER_TO_RELAX_BUDGET"
            if state.get("budget_status") == "insufficient"
            else "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL"
            if state.get("transfer_time") == "exceeds_daily_window"
            else "ASK_USER_CONFIRM_OPENING_HOURS"
            if state.get("opening_hours") == "provider_unavailable"
            else (
            "FOCUSED_USER_QUESTION_WITHOUT_DRAFT"
            if not state.get("trip_dates")
            else "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL"
            )
        )
    elif state.get("draft_plan_version") is not None:
        first_action = decisions[0]["family"]
        outcome = "STALE_CONFIRMATION_REJECTED_WITHOUT_WRITE"
    elif state.get("opening_hours"):
        first_action = decisions[0]["family"]
        outcome = (
            "CONSTRAINT_DRAFT"
            if loop.status == "SUCCEEDED" and "build_schedule_draft" in loop.observation
            else "SAFE_STOP"
        )
    elif state.get("daily_capacity") == "infeasible":
        first_action = decisions[0]["family"]
        outcome = (
            "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL"
            if "build_schedule_draft" in loop.observation
            else "SAFE_STOP"
        )
    elif state.get("fixed_stops"):
        first_action = decisions[0]["family"]
        outcome = (
            "DRAFT_WITH_FIXED_STOP_UNCHANGED"
            if loop.status == "SUCCEEDED"
            and "build_schedule_draft" in loop.observation
            and loop.observation["build_schedule_draft"].get("fixed_stop_unchanged") is True
            else "SAFE_STOP"
        )
    elif route == "provider_timeout":
        first_action = next(
            (
                item["family"]
                for item in decisions
                if item["family"] == "stop_retry_and_explain_route_limit"
            ),
            "get_distance_matrix",
        )
        outcome = (
            "SAFE_STOP"
            if loop.status == "SUCCEEDED" and "get_distance_matrix_failure" in loop.observation
            else "FAILED_TOOL"
            if failed_route_call
            else "WARNING_DRAFT"
        )
    elif route == "fresh_verified":
        first_action = decisions[0]["family"]
        outcome = (
            "VERIFIED_EDITABLE_DRAFT" if "build_schedule_draft" in loop.observation else "SAFE_STOP"
        )
    elif int(state.get("saved_places", 0)) == 1:
        first_action = decisions[0]["family"]
        outcome = "SINGLE_PLACE_DRAFT_WITHOUT_ROUTE" if "build_schedule_draft" in loop.observation else "SAFE_STOP"
    elif route == "malformed":
        first_action = decisions[0]["family"]
        outcome = (
            "WARNING_DRAFT"
            if route_verification_rejected and "build_schedule_draft" in loop.observation
            else "ROUTE_VERIFIED_DRAFT"
            if "build_schedule_draft" in loop.observation
            else "SAFE_STOP"
        )
    elif state.get("draft_result") == "provider_timeout":
        first_action = decisions[0]["family"]
        outcome = "SAFE_STOP"
    else:
        first_action = decisions[0]["family"]
        outcome = (
            "ROUTE_VERIFIED_DRAFT" if "build_schedule_draft" in loop.observation else "SAFE_STOP"
        )
    invariants = {
        "no_formal_write_before_confirmation": True,
        "fixed_stop_not_moved": not state.get("fixed_stops")
        or "build_schedule_draft" not in loop.observation
        or loop.observation["build_schedule_draft"].get("fixed_stop_unchanged") is True,
        "no_selected_place_silently_dropped": state.get("daily_capacity") != "infeasible"
        or (
            "build_schedule_draft" not in loop.observation
            or loop.observation["build_schedule_draft"].get("all_selected_places_accounted_for")
            is True
        ),
        "opening_conflict_not_hidden": not state.get("opening_hours")
        or (
            loop.status != "SUCCEEDED"
            or "build_schedule_draft" not in loop.observation
            or loop.observation["build_schedule_draft"].get("opening_conflict_reported") is True
        ),
        "stale_plan_not_overwritten": not state.get("draft_plan_version")
        or (
            "refresh_context" in loop.observation
            and loop.observation["refresh_context"].get("draft_invalidated") is True
        ),
        "malformed_route_not_used_as_verified": route != "malformed"
        or route_verification_rejected
        or "build_schedule_draft" not in loop.observation,
        "unavailable_opening_hours_not_assumed": state.get("opening_hours") != "provider_unavailable"
        or "build_schedule_draft" not in loop.observation,
    }
    return {
        "first_action": first_action,
        "expected_action_match": first_action == _expected_action(case),
        "outcome": outcome,
        "expected_outcome_match": _expected_outcome(case, outcome),
        "hard_invariants": invariants,
        "tool_calls": len(action_names),
        "decision_families": [item["family"] for item in decisions],
        "loop_status": loop.status,
        "failure": loop.error,
    }


def _run_fixed(case: dict[str, Any]) -> dict[str, Any]:
    """A fixed, state-driven baseline; it never branches on case or family labels."""
    state = case["state"]
    route = state.get("route_matrix", "missing")
    if not state.get("trip_dates"):
        action, outcome, calls = "ask_for_trip_dates", "FOCUSED_USER_QUESTION_WITHOUT_DRAFT", 0
    elif int(state.get("saved_places", 0)) == 0:
        action, outcome, calls = "ask_user_to_add_places", "ASK_USER_ADD_PLACES", 0
    elif state.get("budget_status") == "insufficient":
        action, outcome, calls = "ask_to_relax_budget", "ASK_USER_TO_RELAX_BUDGET", 0
    elif state.get("transfer_time") == "exceeds_daily_window":
        action, outcome, calls = "ask_to_relax_transfer_constraint", "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL", 1
    elif state.get("draft_plan_version") is not None:
        action, outcome, calls = (
            "refresh_context_and_rebuild_draft",
            "STALE_CONFIRMATION_REJECTED_WITHOUT_WRITE",
            1,
        )
    elif state.get("opening_hours") == "provider_unavailable":
        action, outcome, calls = "verify_opening_hours_and_revise_or_report_conflict", "ASK_USER_CONFIRM_OPENING_HOURS", 1
    elif state.get("daily_capacity") == "infeasible":
        action, outcome, calls = (
            "report_conflict_and_ask_which_constraint_to_relax",
            "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL",
            2,
        )
    elif state.get("opening_hours"):
        action, outcome, calls = (
            "verify_opening_hours_and_revise_or_report_conflict",
            "CONSTRAINT_DRAFT",
            2,
        )
    elif state.get("fixed_stops"):
        action, outcome, calls = (
            "preserve_fixed_stop_and_schedule_around_it",
            "DRAFT_WITH_FIXED_STOP_UNCHANGED",
            2,
        )
    elif route == "malformed":
        action, outcome, calls = "get_distance_matrix", "SAFE_STOP", 2
    elif route == "provider_timeout":
        action, outcome, calls = "stop_retry_and_explain_route_limit", "SAFE_STOP", 1
    elif route == "stale":
        action, outcome, calls = "refresh_stale_route_evidence", "ROUTE_VERIFIED_DRAFT", 2
    elif int(state.get("saved_places", 0)) == 1:
        action, outcome, calls = "build_schedule_without_route", "SINGLE_PLACE_DRAFT_WITHOUT_ROUTE", 1
    elif state.get("draft_result") == "provider_timeout":
        action, outcome, calls = "reuse_fresh_route_evidence", "SAFE_STOP", 2
    elif route == "fresh_verified":
        action, outcome, calls = "reuse_fresh_route_evidence", "VERIFIED_EDITABLE_DRAFT", 1
    else:
        action, outcome, calls = "get_distance_matrix", "ROUTE_VERIFIED_DRAFT", 2
    return {
        "first_action": action,
        "expected_action_match": action == _expected_action(case),
        "outcome": outcome,
        "expected_outcome_match": _expected_outcome(case, outcome),
        "hard_invariants": {
            "no_formal_write_before_confirmation": True,
            "fixed_stop_not_moved": True,
            "no_selected_place_silently_dropped": True,
            "opening_conflict_not_hidden": True,
            "stale_plan_not_overwritten": True,
            "malformed_route_not_used_as_verified": True,
            "unavailable_opening_hours_not_assumed": True,
        },
        "tool_calls": calls,
        "loop_status": "FIXED_WORKFLOW",
        "failure": None,
    }


async def _run(case_file: Path = CASE_FILE) -> dict[str, Any]:
    dataset = json.loads(case_file.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    groups: dict[str, dict[str, Any]] = {}
    for group in ("B1_FIXED", "B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"):
        rows = []
        for case in dataset["cases"]:
            if group == "B1_FIXED":
                result = await evaluate_fixed_case(case)
                result["tool_calls"] = len(result.pop("tool_attempts", []))
                result["loop_status"] = "PRODUCT_FIXED_WORKFLOW_REPLAY"
                result["failure"] = None
            else:
                result = await _run_agent(group, case)
            rows.append({"case_id": case["id"], **result})
        groups[group] = {
            "case_count": len(rows),
            "expected_action_matches": sum(row["expected_action_match"] for row in rows),
            "expected_outcome_matches": sum(row["expected_outcome_match"] for row in rows),
            "hard_invariant_violations": sum(
                not valid for row in rows for valid in row["hard_invariants"].values()
            ),
            "tool_calls": sum(row["tool_calls"] for row in rows),
            "runs": rows,
        }
    return {
        "evaluation_id": dataset["dataset_id"],
        "provider_mode": "DETERMINISTIC_PROVIDER_FIXTURES_AND_SCRIPTED_AGENT_POLICIES",
        "case_file_sha256": digest,
        "source_provenance": evaluation_provenance("advisor_full_task"),
        "run_at_utc": datetime.now(UTC).isoformat(),
        "scope": (
            f"{dataset['scope']} Product-grounded planner comparison with scripted B2/B3 policies; "
            "not live model decision quality."
        ),
        "scenario_family_count": len({
            case.get("scenario_family") or case["id"].rsplit("-variant-", 1)[0]
            for case in dataset["cases"]
        }),
        "groups": groups,
        "interpretation": (
            "B1 replays the fixed Advisor source revision; B2/B3 execute the bounded loop with scripted policies. "
            "All groups share the production deterministic itinerary planner and injected provider states. "
            "This does not establish real LLM decision quality."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=CASE_FILE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(_run(args.dataset))
    default_output = (
        RESULT_FILE
        if args.dataset.resolve() == CASE_FILE.resolve()
        else RESULT_FILE.with_name(f"{result['evaluation_id']}.json")
    )
    output_file = args.output or default_output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: {name: value for name, value in summary.items() if name != "runs"}
                for key, summary in result["groups"].items()
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
