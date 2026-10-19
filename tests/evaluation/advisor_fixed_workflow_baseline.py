"""Replay the pre-Agent Advisor fixed scheduling workflow on frozen task states.

This is a product-grounded B1 baseline: it uses the planner and tool runtime
from the fixed Advisor implementation at BASELINE_REVISION, while providers
are deterministic fault-injection fixtures. It is still not a live-model test.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v3.json"
RESULT = ROOT / "tests/evaluation/results/advisor_fixed_workflow_baseline_v1.json"
BASELINE_REVISION = "0c4a8998896bc5b27877ff9f49cfbf77ec0128d7"


class MatrixInput(BaseModel):
    points: list[tuple[float, float]]


async def _trace(_entry: dict) -> None:
    return None


def _matrix_for_state(state: dict[str, Any], size: int) -> list[list[int]]:
    route_state = state.get("route_matrix", "missing")
    if route_state == "provider_timeout":
        raise ToolFailure("MAP_TIMEOUT")
    if route_state == "malformed":
        # Shape is valid but negative durations are not. The legacy planner
        # checks matrix dimensions only, so this tests its missing verifier.
        return [[0 if row == col else -30 for col in range(size)] for row in range(size)]
    if state.get("transfer_time") == "exceeds_daily_window":
        return [[0 if row == col else 900 for col in range(size)] for row in range(size)]
    return [[0 if row == col else 8 + abs(row - col) * 3 for col in range(size)] for row in range(size)]


def _candidates(case_id: str, state: dict[str, Any], size: int) -> list[Candidate]:
    candidates = []
    for index in range(size):
        hours = None
        if index == 0 and state.get("opening_hours") == "one_place_closes_before_visit":
            hours = {"open": "09:00", "close": "09:30"}
        candidates.append(Candidate(
            poi_id=uuid5(NAMESPACE_URL, f"travelpilot-fixed-baseline:{case_id}:{index}"),
            name=f"Fixture place {index + 1}",
            latitude=30.6 + index * 0.001,
            longitude=104.0 + index * 0.001,
            duration_min=60,
            priority=3,
            interest_score=0.5,
            opening_hours=hours,
        ))
    return candidates


def build_schedule_fixture(
    case_id: str, state: dict[str, Any], matrix: list[list[int]] | None,
) -> dict[str, Any]:
    """Run the same deterministic planner used by the product on fixture data."""
    dates = state.get("trip_dates")
    place_count = int(state.get("saved_places", 0))
    if not dates or place_count < 1:
        return {
            "feasible": False,
            "candidate_ids": [],
            "all_selected_places_accounted_for": False,
            "route_warning": place_count > 1 and matrix is None,
            "warnings": [],
        }
    if state.get("draft_result") == "provider_timeout":
        raise TimeoutError("injected local planner failure")
    candidates = _candidates(case_id, state, place_count)
    visits, warnings = build_itinerary(
        date.fromisoformat(dates[0]),
        date.fromisoformat(dates[-1]),
        candidates,
        matrix,
        buffer_min=90 if int(state.get("pace_level", 3)) <= 2 else 60,
        transfer_buffer_min=15,
        max_pois_per_day={1: 3, 2: 4, 3: 5, 4: 6, 5: 6}.get(
            int(state.get("pace_level", 3)), 4
        ),
    )
    candidate_ids = [str(visit.candidate.poi_id) for visit in visits]
    return {
        "feasible": len(visits) == place_count,
        "candidate_ids": candidate_ids,
        "all_selected_places_accounted_for": len(visits) == place_count,
        "route_warning": place_count > 1 and matrix is None,
        "warnings": warnings,
        "visits": [{
            "candidate_index": next(
                index for index, candidate in enumerate(candidates)
                if candidate.poi_id == visit.candidate.poi_id
            ),
            "day": visit.day.isoformat(),
            "start_time": visit.start_time.strftime("%H:%M"),
            "end_time": visit.end_time.strftime("%H:%M"),
        } for visit in visits],
    }


async def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    state = case["state"]
    dates = state.get("trip_dates")
    place_count = int(state.get("saved_places", 0))
    route_state = state.get("route_matrix", "missing")
    action = "build_schedule_without_route" if place_count == 1 else "get_distance_matrix"
    outcome = "SAFE_STOP"
    warnings: list[str] = []
    visits: list[dict[str, Any]] = []
    matrix: list[list[int]] | None = None
    calls: list[dict[str, Any]] = []

    if not dates:
        action = "ask_for_trip_dates"
        outcome = "FOCUSED_USER_QUESTION_WITHOUT_DRAFT"
    elif place_count == 0:
        action = "ask_user_to_add_places"
        outcome = "ASK_USER_ADD_PLACES"
    elif state.get("draft_plan_version") is not None:
        action = "refresh_context_and_rebuild_draft"
        outcome = "STALE_CONFIRMATION_REJECTED_WITHOUT_WRITE"
    elif place_count > 29:
        action = "ask_to_reduce_place_count"
        outcome = "SAFE_STOP"
    else:
        candidates = _candidates(case["id"], state, place_count)
        async def get_matrix(args: MatrixInput) -> dict[str, Any]:
            return {"durations_min": _matrix_for_state(state, len(args.points))}

        runtime = AgentRuntime(
            [ToolSpec("get_distance_matrix", MatrixInput, get_matrix, retry_limit=0)],
            _trace,
            max_steps=1,
        )
        if place_count > 1:
            try:
                result = await runtime.call("get_distance_matrix", {
                    "points": [(item.latitude, item.longitude) for item in candidates],
                })
                matrix = result["durations_min"]
            except ToolFailure:
                calls = runtime.calls
                action = "stop_retry_and_explain_route_limit"
                outcome = "SAFE_STOP"
                matrix = None
        if outcome != "SAFE_STOP" or not calls:
            try:
                proposal = build_schedule_fixture(case["id"], state, matrix)
                visits = proposal["visits"]
                warnings = proposal["warnings"]
                all_places_scheduled = proposal["all_selected_places_accounted_for"]
                if all_places_scheduled:
                    outcome = (
                        "SINGLE_PLACE_DRAFT_WITHOUT_ROUTE"
                        if place_count == 1
                        else "VERIFIED_EDITABLE_DRAFT"
                        if route_state == "fresh_verified"
                        else "ROUTE_VERIFIED_DRAFT"
                    )
                elif state.get("transfer_time") == "exceeds_daily_window":
                    action = "ask_to_relax_transfer_constraint"
                    outcome = "INFEASIBILITY_REPORT_WITHOUT_SILENT_REMOVAL"
                else:
                    outcome = "SAFE_STOP"
                if state.get("draft_plan_version") is not None:
                    action = "refresh_context_and_rebuild_draft"
                    outcome = "STALE_CONFIRMATION_REJECTED_WITHOUT_WRITE"
            except (ValueError, TimeoutError):
                outcome = "SAFE_STOP"
            calls = runtime.calls

    fixed_stops = state.get("fixed_stops")
    fixed_appointment_unchanged = True
    if isinstance(fixed_stops, list) and fixed_stops and visits:
        # The legacy input contract did not bind an appointment to a POI ID;
        # evaluate the fixture against the first confirmed stop.
        appointment = fixed_stops[0]
        first_stop = next((item for item in visits if item["candidate_index"] == 0), None)
        fixed_appointment_unchanged = bool(
            first_stop
            and first_stop["day"] == appointment.get("day")
            and first_stop["start_time"] == appointment.get("start")
        )
    is_draft = outcome in {
        "ROUTE_VERIFIED_DRAFT", "VERIFIED_EDITABLE_DRAFT",
        "SINGLE_PLACE_DRAFT_WITHOUT_ROUTE", "DRAFT_WITH_FIXED_STOP_MOVED",
    }
    if is_draft and not fixed_appointment_unchanged:
        outcome = "DRAFT_WITH_FIXED_STOP_MOVED"
    route_valid = matrix is None or verify_distance_matrix(
        {"durations_min": matrix}, place_count
    )
    invariants = {
        "no_formal_write_before_confirmation": True,
        "all_selected_places_accounted_for": not is_draft or len(visits) == place_count,
        "fixed_stop_not_moved": fixed_appointment_unchanged,
        "malformed_route_not_used_as_verified": route_state != "malformed" or matrix is None or route_valid,
        "opening_conflict_not_hidden": state.get("opening_hours") != "one_place_closes_before_visit"
        or not is_draft or any("超出营业时间" in warning for warning in warnings),
        "unavailable_opening_hours_not_assumed": state.get("opening_hours") != "provider_unavailable"
        or not is_draft,
        "insufficient_budget_not_ignored": state.get("budget_status") != "insufficient" or not is_draft,
        "stale_plan_not_overwritten": True,
    }
    expected = case["expected_outcome"].upper()
    outcome_match = outcome == expected
    if expected in {"WARNING_DRAFT_OR_SAFE_STOP", "ESTIMATED_DRAFT_WITH_ROUTE_WARNING_OR_SAFE_STOP"}:
        outcome_match = outcome in {"SAFE_STOP", "WARNING_DRAFT"}
    if expected == "CONSTRAINT_REPORTED_DRAFT_OR_SAFE_STOP":
        outcome_match = outcome in {"SAFE_STOP", "CONSTRAINT_DRAFT"}
    return {
        "case_id": case["id"],
        "first_action": action,
        "expected_action_match": action == case["expected_next_action"],
        "outcome": outcome,
        "expected_outcome_match": outcome_match,
        "tool_attempts": calls,
        "visits": visits,
        "warnings": warnings,
        "hard_invariants": invariants,
    }


async def run(dataset_path: Path = DATASET) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    rows = [await evaluate_case(case) for case in dataset["cases"]]
    return {
        "evaluation_id": "advisor_fixed_workflow_baseline_v1",
        "provider_mode": "FIXED_ADVISOR_WITH_DETERMINISTIC_FAULT_FIXTURES",
        "baseline_source_revision": BASELINE_REVISION,
        "baseline_source": "apps/api/src/travelpilot_api/api/v1/advisor.py::send_message fixed schedule branch",
        "planner_source": "packages/core/src/travelpilot_core/engines/optimizer/itinerary.py::build_itinerary",
        "dataset": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
        "dataset_sha256": digest,
        "case_count": len(rows),
        "expected_action_matches": sum(row["expected_action_match"] for row in rows),
        "expected_outcome_matches": sum(row["expected_outcome_match"] for row in rows),
        "hard_invariant_violations": sum(
            not valid for row in rows for valid in row["hard_invariants"].values()
        ),
        "tool_attempts": sum(len(row["tool_attempts"]) for row in rows),
        "limitations": [
            "This is a replay of the committed fixed workflow, not a live API or LLM run.",
            "Provider responses and synthetic POI properties are deterministic fault-injection fixtures.",
            "A fixed appointment fixture is associated with candidate index zero because the frozen case format has no POI association field.",
            "Do not compare these scores directly with B2/B3 scripted-loop scores as an apples-to-apples experiment.",
        ],
        "runs": rows,
        "run_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    report = asyncio.run(run(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"runs", "limitations"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
