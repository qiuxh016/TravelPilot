"""Bounded live-model continuation evaluation on frozen two-turn tasks.

The runner makes two model decisions per case and variant. It uses only frozen
synthetic user inputs, allowlisted tool schemas, and local fixture observations;
it never writes TravelPilot data or calls map/weather providers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from travelpilot_api.advisor.route_query import RouteQueryInput, _verified_route
from travelpilot_api.advisor.search_loop import _verified_search_result
from travelpilot_api.advisor.weather_evidence import valid_weather_evidence
from travelpilot_api.settings import settings
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

from tests.evaluation.advisor_multiturn_contracts import (
    _is_holdout_dataset,
    load_holdout_references,
    validate_dataset,
    validate_holdout_separation,
)
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_v5.json"
RESULT = ROOT / "tests/evaluation/results/advisor_multiturn_live_v5_20261019.json"
CASE_IDS = (
    "discover-low-information-01",
    "discover-popular-places-02",
    "schedule-missing-dates-03",
    "meal-gap-ask-choice-06",
    "weather-ask-dates-08",
)
VARIANTS = ("B2_AGENT_LOOP", "B3_VERIFIED_RECOVERY_LOOP")
TOKEN_CAP = 128
ADDITIONAL_CASE_IDS = (
    "schedule-missing-dates-03",
    "weather-provider-outage-09",
    "route-ambiguous-endpoints-10",
    "lodging-dates-missing-34",
    "cross-weather-accessibility-57",
)


def _holdout_exposure_marker(dataset_id: str) -> Path:
    return ROOT / "tests/evaluation/results" / f".{dataset_id}.exposure.json"

class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SearchPlacesArguments(_StrictArguments):
    keyword: str = Field(min_length=1, max_length=100)
    city: str | None = Field(default=None, max_length=100)


class _MatrixArguments(_StrictArguments):
    points: list[tuple[float, float]] = Field(min_length=2, max_length=30)


class _NoArguments(_StrictArguments):
    pass


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "search_places": _SearchPlacesArguments,
    "get_trip_weather": _NoArguments,
    "get_distance_matrix": _MatrixArguments,
    "build_schedule_draft": _NoArguments,
    "get_trip_route": RouteQueryInput,
}
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    name: {"input_schema": model.model_json_schema()}
    for name, model in TOOL_ARGUMENT_MODELS.items()
}


def _validate_tool_arguments(
    tool: str | None,
    arguments: Any,
    trip_context: dict[str, Any] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    model = TOOL_ARGUMENT_MODELS.get(tool or "")
    if model is None or not isinstance(arguments, dict):
        return False, [{"path": [], "type": "unknown_tool_or_non_object_arguments"}]
    try:
        model.model_validate(arguments)
    except ValidationError as exc:
        errors = [
            {"path": list(item["loc"]), "type": str(item["type"])[:80]}
            for item in exc.errors(include_input=False)[:8]
        ]
        return False, errors
    context = trip_context or {}
    if trip_context is not None and tool == "get_trip_route":
        known_places = context.get("known_places")
        known_ids = {
            place.get("id") for place in known_places
            if isinstance(place, dict) and isinstance(place.get("id"), str)
        } if isinstance(known_places, list) else set()
        origin_id = arguments["origin_place_id"]
        destination_id = arguments["destination_place_id"]
        if not known_ids:
            return False, [{"path": ["known_places"], "type": "missing_route_endpoint_evidence"}]
        if origin_id not in known_ids or destination_id not in known_ids:
            return False, [{"path": ["origin_place_id", "destination_place_id"], "type": "route_endpoint_not_in_known_places"}]
        if origin_id == destination_id:
            return False, [{"path": ["destination_place_id"], "type": "route_endpoints_must_differ"}]
    elif trip_context is not None and tool == "get_distance_matrix":
        saved_places = context.get("saved_place_details")
        points = arguments["points"]
        if not isinstance(saved_places, list) or len(saved_places) != len(points):
            return False, [{"path": ["points"], "type": "matrix_points_not_grounded_in_saved_places"}]
        expected_points = []
        for place in saved_places:
            if not isinstance(place, dict):
                return False, [{"path": ["saved_place_details"], "type": "invalid_saved_place_evidence"}]
            try:
                expected_points.append((float(place["latitude"]), float(place["longitude"])))
            except (KeyError, TypeError, ValueError):
                return False, [{"path": ["saved_place_details"], "type": "missing_saved_place_coordinates"}]
        if any(
            abs(actual[axis] - expected[axis]) > 1e-4
            for actual, expected in zip(points, expected_points, strict=True)
            for axis in (0, 1)
        ):
            return False, [{"path": ["points"], "type": "matrix_coordinates_do_not_match_saved_places"}]
    return True, []


def _should_append_fixture_observation(row: dict[str, Any], allowed_tools: list[str]) -> bool:
    action = row.get("action") or {}
    return (
        row.get("action_valid") is True
        and action.get("kind") == "CALL_TOOL"
        and action.get("tool") in allowed_tools
    )

EXPECTED_TOOL = {
    "SEARCH_PLACES": "search_places",
    "REFINE_CANDIDATES": "search_places",
    "GET_TRIP_WEATHER": "get_trip_weather",
    "GET_DISTANCE_MATRIX": "get_distance_matrix",
    "GET_TRIP_ROUTE": "get_trip_route",
    "BUILD_SCHEDULE_DRAFT": "build_schedule_draft",
}

# These expected labels describe domain state transitions, not AgentAction
# values in the current public action schema. They cannot be scored as a
# single model decision until the production contract exposes those actions.
UNSCORABLE_EXPECTED_ACTIONS = {
    "FOLLOW_CURRENT_REQUEST",
    "UPDATE_TASK_CONSTRAINTS",
}
ASK_OR_DISCLOSE_ACTIONS = {
    "ASK_USER_OR_LABEL_ASSUMPTION",
    "ASK_USER_OR_LABEL_UNVERIFIED",
}


def _fixture_observation(
    tool: str, case: dict[str, Any], arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a clearly synthetic read-only result for the next decision."""
    state = case.get("initial_state", {})
    if (
        tool in {"get_trip_route", "get_distance_matrix"}
        and state.get("route_provider") == "timeout"
    ):
        return {
            "status": "FAILED_FIXTURE",
            "error_code": "MAP_TIMEOUT",
            "message": "Synthetic provider timeout; no route evidence was returned.",
            "source": "LOCAL_FIXTURE",
        }
    if tool == "search_places":
        return {
            "status": "SIMULATED",
            "places": [
                {"provider_id": "fixture-place-a", "name": "评测地点甲", "latitude": 30.1,
                 "longitude": 104.1, "provider": "fake"},
                {"provider_id": "fixture-place-b", "name": "评测地点乙", "latitude": 30.2,
                 "longitude": 104.2, "provider": "fake"},
            ],
        }
    if tool == "get_trip_weather":
        dates = state.get("trip_dates")
        if not isinstance(dates, list) or not dates:
            dates = ["2026-11-02"]
        return {
            "status": "AVAILABLE",
            "source": "LOCAL_FIXTURE",
            "city": state.get("destination", "评测城市"),
            "fetched_at": "2027-01-01T00:00:00+00:00",
            "days": [
                {
                    "date": day,
                    "description": "多云",
                    "precipitation_probability_max": 20,
                    "temperature_min_c": 16,
                    "temperature_max_c": 23,
                }
                for day in dates[:7]
            ],
        }
    if tool == "get_distance_matrix":
        expected_size = _expected_schedule_stop_count(case)
        durations = [
            [0 if row == column else 10 + abs(row - column) * 4
             for column in range(expected_size)]
            for row in range(expected_size)
        ]
        return {
            "status": "VERIFIED_FIXTURE",
            "source": "LOCAL_FIXTURE",
            "point_count": expected_size,
            "durations_min": durations,
        }
    if tool == "get_trip_route":
        args = arguments or {}
        places = state.get("known_places", [])
        by_id = {place.get("id"): place for place in places if isinstance(place, dict)}
        origin = by_id.get(args.get("origin_place_id"))
        destination = by_id.get(args.get("destination_place_id"))
        if origin and destination and all(key in point for point in (origin, destination)
                                         for key in ("latitude", "longitude")):
            points = [
                [float(origin["latitude"]), float(origin["longitude"])],
                [float(destination["latitude"]), float(destination["longitude"])],
            ]
            return {
                "status": "SIMULATED", "duration_min": 18, "distance_m": 1200,
                "source": "Local simulated maps", "origin_place_id": args.get("origin_place_id"),
                "destination_place_id": args.get("destination_place_id"),
                "mode": args.get("mode"), "points": points, "polyline": points,
                "retrieved_at": "2027-01-01T00:00:00+00:00",
            }
        return {"status": "VERIFIED_FIXTURE", "duration_min": 18, "source": "LOCAL_FIXTURE"}
    if tool == "build_schedule_draft":
        places = state.get("saved_place_details", [])
        candidate_ids = [str(place.get("id", f"fixture-{index}"))
                         for index, place in enumerate(places)]
        return {
            "status": "DRAFT_FIXTURE",
            "feasible": True, "candidate_ids": candidate_ids,
            "schedules": [{"candidate_id": value} for value in candidate_ids],
            "source": "LOCAL_FIXTURE", "formal_write": False,
        }
    return {"status": "NO_RESULT", "source": "LOCAL_FIXTURE"}


def _expected_schedule_stop_count(case: dict[str, Any]) -> int:
    state = case.get("initial_state", {})
    count = state.get("saved_places") if isinstance(state, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return 2
    return min(count, 29)


def _schedule_route_evidence_state(
    case: dict[str, Any], history: list[dict[str, Any]],
) -> str:
    expected_size = _expected_schedule_stop_count(case)
    if expected_size < 2:
        return "NOT_REQUIRED"
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("role") != "tool_observation":
            continue
        if item.get("tool") != "get_distance_matrix":
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            return "INVALID"
        if (
            result.get("status") == "VERIFIED_FIXTURE"
            and result.get("source") == "LOCAL_FIXTURE"
            and result.get("point_count") == expected_size
            and verify_distance_matrix(result, expected_size)
        ):
            return "VERIFIED"
        # The production scheduler permits a warned estimate after an explicit
        # route-provider failure, but never after malformed or absent evidence.
        return "FAILED" if (
            result.get("status") == "FAILED_FIXTURE"
            and result.get("error_code") == "MAP_TIMEOUT"
            and result.get("source") == "LOCAL_FIXTURE"
        ) else "INVALID"
    return "MISSING"


def _allowed_tool_schemas(
    case: dict[str, Any], turn: dict[str, Any], history: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    allowed = set(turn["allowed_tools"]) & set(TOOL_SCHEMAS)
    if "build_schedule_draft" in allowed:
        route_state = _schedule_route_evidence_state(case, history)
        if route_state in {"VERIFIED", "NOT_REQUIRED"}:
            allowed.discard("get_distance_matrix")
        elif route_state != "FAILED":
            allowed.discard("build_schedule_draft")
    return {name: TOOL_SCHEMAS[name] for name in sorted(allowed)}


def _expected_match(turn: dict[str, Any], action: dict[str, Any]) -> bool | None:
    expected_values = turn.get("acceptable_actions", [turn["expected_action"]])
    if not isinstance(expected_values, list) or not expected_values:
        return False
    if all(expected in UNSCORABLE_EXPECTED_ACTIONS for expected in expected_values):
        return None
    for expected in expected_values:
        if expected == "FINISH" and action.get("kind") == "FINISH":
            return True
        has_text = bool(action.get("question") or action.get("summary"))
        if (
            expected.startswith("ASK_USER") and expected not in ASK_OR_DISCLOSE_ACTIONS
            and action.get("kind") == "ASK_USER" and has_text
        ):
            return True
        if expected in ASK_OR_DISCLOSE_ACTIONS and action.get("kind") in {"ASK_USER", "FINISH"} and has_text:
            return True
        expected_tool = EXPECTED_TOOL.get(expected)
        if expected_tool and action.get("kind") == "CALL_TOOL" and action.get("tool") == expected_tool:
            return True
    return False


async def _run_verified_loop(action: AgentAction, case: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    """Execute B3 actions with the production loop, evidence verifier and safe recovery."""
    calls: list[str] = []
    checkpoints: list[dict[str, Any]] = []
    tools = []
    for name in allowed:
        async def handler(args: BaseModel, tool_name: str = name) -> dict[str, Any]:
            calls.append(tool_name)
            fixture = _fixture_observation(tool_name, case)
            if fixture.get("status") == "FAILED_FIXTURE":
                raise ToolFailure(str(fixture.get("error_code", "FIXTURE_FAILURE")))
            if tool_name == "get_trip_route":
                context_places = case.get("initial_state", {}).get("known_places", [])
                by_id = {place.get("id"): place for place in context_places if isinstance(place, dict)}
                route_args = args.model_dump()
                origin = by_id.get(route_args["origin_place_id"])
                destination = by_id.get(route_args["destination_place_id"])
                if not origin or not destination:
                    return {"status": "MALFORMED_FIXTURE", "source": "LOCAL_FIXTURE"}
                points = [
                    [float(origin["latitude"]), float(origin["longitude"])],
                    [float(destination["latitude"]), float(destination["longitude"])],
                ]
                fixture = {
                    "status": "SIMULATED", "source": "Local simulated maps",
                    "origin_place_id": route_args["origin_place_id"],
                    "destination_place_id": route_args["destination_place_id"],
                    "mode": route_args["mode"], "distance_m": 1200, "duration_min": 18,
                    "points": points, "polyline": points,
                    "retrieved_at": "2027-01-01T00:00:00+00:00",
                }
            return fixture
        tools.append(ToolSpec(name, TOOL_ARGUMENT_MODELS[name], handler, retry_limit=0))

    async def trace(_event: dict[str, Any]) -> None:
        return None

    async def checkpoint(event: dict[str, Any]) -> None:
        checkpoints.append(event)

    async def verifier(name: str, output: dict[str, Any]) -> bool:
        if output.get("source") not in {None, "LOCAL_FIXTURE", "Local simulated maps"}:
            return False
        if name == "get_distance_matrix":
            return verify_distance_matrix(output, _expected_schedule_stop_count(case))
        if name == "search_places":
            return _verified_search_result(output)
        if name == "get_trip_weather":
            return valid_weather_evidence(output, expected_source="LOCAL_FIXTURE")
        if name == "get_trip_route":
            known = case.get("initial_state", {}).get("known_places", [])
            approved = {
                place.get("id"): place for place in known
                if isinstance(place, dict) and place.get("id")
            }
            return _verified_route(output, approved)
        if name == "build_schedule_draft":
            schedules = output.get("schedules")
            expected_ids = output.get("candidate_ids")
            actual_ids = [item.get("candidate_id") for item in schedules or []
                          if isinstance(item, dict)]
            return (
                output.get("feasible") is True and isinstance(schedules, list)
                and isinstance(expected_ids, list) and len(actual_ids) == len(expected_ids)
                and set(actual_ids) == set(expected_ids) and output.get("formal_write") is False
            )
        return False

    async def policy(observation: dict[str, Any], _allowed: list[str]) -> AgentAction:
        if any(key.endswith(("_failure", "_verification_failure")) for key in observation):
            return AgentAction(kind="FINISH", summary="本地证据未通过校验，安全停止且不重复调用。")
        if calls:
            return AgentAction(kind="FINISH", summary="本地工具结果已校验；没有写入正式行程。")
        return action

    async def recover(observation: dict[str, Any], _allowed: list[str], _decision: int):
        if any(key.endswith(("_failure", "_verification_failure")) for key in observation):
            return AgentAction(kind="FINISH", summary="读取失败或证据无效，停止重试并保留安全状态。")
        return None

    runtime = AgentRuntime(tools, trace, max_steps=1, max_attempts=1, token_budget=0)
    result = await run_constrained_loop(
        runtime=runtime, policy=policy, verifier=verifier, checkpoint=checkpoint,
        initial_observation={"evaluation_fixture": True}, max_decisions=2,
        allowed_tool_names=set(allowed), continue_on_tool_failure=True,
        continue_on_verifier_failure=True, recover_decision=recover,
    )
    failure_seen = any(item.get("state") in {"FAILED", "REJECTED"} for item in checkpoints)
    return {"loop_status": result.status, "loop_error": result.error,
            "tool_calls": calls, "checkpoints": checkpoints,
            "verified_tool_count": sum(item.get("status") == "VERIFIED" for item in result.transitions),
            "safe_stop": failure_seen and result.status == "SUCCEEDED",
            "observations": {
                key: value for key, value in result.observation.items()
                if key in allowed or key.endswith(("_failure", "_verification_failure"))
            }}


def rescore_report(report: dict[str, Any]) -> dict[str, Any]:
    """Re-score stored model actions against current action/tool contracts.

    This is an offline transformation: it does not call the model or tools.
    Raw actions and token usage remain unchanged. Tool argument schemas are
    checked independently of the model's CALL_TOOL envelope.
    """
    rescored = json.loads(json.dumps(report))
    summary = {}
    for arm, cases in rescored.get("groups", {}).items():
        for case in cases:
            for turn in case.get("turns", []):
                action = turn.get("action") or {}
                if action.get("kind") == "CALL_TOOL":
                    arguments_valid, errors = _validate_tool_arguments(
                        action.get("tool"), action.get("arguments"),
                        case.get("initial_state"),
                    )
                    turn["tool_arguments_valid"] = arguments_valid
                    turn["argument_validation_errors"] = errors
                    turn["action_valid"] = bool(turn.get("action_valid", True)) and arguments_valid
                turn["expected_action_match"] = _expected_match(turn, action)
                expected_values = turn.get("acceptable_actions", [turn.get("expected_action")])
                if not turn.get("action_valid", True) and any(value in EXPECTED_TOOL for value in expected_values):
                    turn["expected_action_match"] = False
            matches = [turn.get("expected_action_match") for turn in case.get("turns", [])]
            case["action_sequence_scoreable"] = all(match is not None for match in matches)
            case["action_sequence_matches"] = all(matches) if case["action_sequence_scoreable"] else None
        turns = [turn for case in cases for turn in case.get("turns", [])]
        scoreable_turns = [turn for turn in turns if turn.get("expected_action_match") is not None]
        scoreable_cases = [case for case in cases if case.get("action_sequence_scoreable")]
        summary[arm] = {
            "case_count": len(cases),
            "decision_count": len(turns),
            "scoreable_decision_count": len(scoreable_turns),
            "unscorable_decision_count": len(turns) - len(scoreable_turns),
            "expected_action_matches": sum(
                turn["expected_action_match"] is True for turn in scoreable_turns
            ),
            "action_sequence_scoreable_count": len(scoreable_cases),
            "action_sequences_matched": sum(
                case["action_sequence_matches"] is True for case in scoreable_cases
            ),
            "allowlist_violations": sum(not turn.get("allowlist_valid", False) for turn in turns),
            "invalid_actions": sum(not turn.get("action_valid", False) for turn in turns),
            "output_tokens": sum(turn.get("output_tokens", 0) for turn in turns),
            "prompt_tokens": sum(turn.get("prompt_tokens", 0) for turn in turns),
            "runs": cases,
        }
    rescored["summary"] = summary
    rescored["scoring_revision"] = {
        "id": "adjudicated_acceptable_action_sets_v4",
        "model_requests_repeated": False,
        "rescorer_source": "tests/evaluation/advisor_multiturn_live_eval.py",
        "rescorer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "note": "Accepted actions are adjudicated per turn; tool arguments are independently validated; safe disclosure is accepted only for explicitly annotated alternatives.",
    }
    return rescored


async def _decide(
    *,
    case: dict[str, Any],
    turn_index: int,
    turn: dict[str, Any],
    variant: str,
    history: list[dict[str, Any]],
    token_cap: int = TOKEN_CAP,
) -> dict[str, Any]:
    schemas = _allowed_tool_schemas(case, turn, history)
    allowed = sorted(schemas)
    observation: dict[str, Any] = {
        "task": case["task_type"],
        "user_goal": case["turns"][0]["user"],
        "current_user_message": turn["user"],
        "turn_number": turn_index + 1,
        "trip_context": case["initial_state"],
        "conversation_history": history[-6:],
        "available_tools": allowed,
        "tool_observations_are_local_fixtures": True,
        "policy": "Use only available tools. Ask for missing dates or preferences; never commit changes or claim fixture facts are live provider data.",
    }
    if variant == "B3_VERIFIED_RECOVERY_LOOP":
        observation["execution_contract"] = (
            "Your selected action will be run through the production constrained loop. "
            "Tool results are synthetic local fixtures; no formal trip data can be written."
        )
    try:
        decision, usage = await decide_next_action(
            observation,
            schemas,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            token_budget=token_cap,
        )
        action = decision.model_dump(mode="json")
        allowlist_valid = action.get("kind") != "CALL_TOOL" or action.get("tool") in schemas
        tool_arguments_valid = True
        argument_validation_errors: list[dict[str, Any]] = []
        if action.get("kind") == "CALL_TOOL":
            tool_arguments_valid, argument_validation_errors = _validate_tool_arguments(
                action.get("tool"), action.get("arguments"), case.get("initial_state"),
            )
        if action.get("kind") == "CALL_TOOL" and action.get("tool") == "search_places":
            args = action.get("arguments") or {}
            destination = case["initial_state"].get("destination")
            allowlist_valid = (
                allowlist_valid
                and isinstance(args.get("keyword"), str)
                and (not destination or args.get("city") in {None, destination})
            )
        loop_result = None
        if variant == "B3_VERIFIED_RECOVERY_LOOP":
            loop_result = await _run_verified_loop(
                AgentAction.model_validate(action), case, allowed,
            )
        return {
            "case_id": case["id"],
            "turn": turn_index + 1,
            "expected_action": turn["expected_action"],
            "acceptable_actions": turn.get("acceptable_actions", [turn["expected_action"]]),
            "action": action,
            "expected_action_match": (
                _expected_match(turn, action) and tool_arguments_valid
                if action.get("kind") == "CALL_TOOL" and _expected_match(turn, action) is True
                else _expected_match(turn, action)
            ),
            "action_valid": tool_arguments_valid,
            "tool_arguments_valid": tool_arguments_valid,
            "argument_validation_errors": argument_validation_errors,
            "allowlist_valid": allowlist_valid,
            "request_status": "SUCCEEDED",
            "prompt_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "execution": loop_result,
        }
    except PlannerDecisionFailure as exc:
        return {
            "case_id": case["id"],
            "turn": turn_index + 1,
            "expected_action": turn["expected_action"],
            "acceptable_actions": turn.get("acceptable_actions", [turn["expected_action"]]),
            "action": None,
            "expected_action_match": _expected_match(turn, {}),
            "action_valid": False,
            "allowlist_valid": True,
            "request_status": exc.code,
            "prompt_tokens": exc.usage.prompt_tokens,
            "output_tokens": exc.usage.completion_tokens,
            "total_tokens": exc.usage.total_tokens,
            "execution": None,
        }
    except ToolFailure as exc:
        return {
            "case_id": case["id"],
            "turn": turn_index + 1,
            "expected_action": turn["expected_action"],
            "acceptable_actions": turn.get("acceptable_actions", [turn["expected_action"]]),
            "action": None,
            "expected_action_match": _expected_match(turn, {}),
            "action_valid": False,
            "allowlist_valid": True,
            "request_status": exc.code,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "execution": None,
        }


async def run_live(
    dataset_path: Path = DATASET,
    case_ids: tuple[str, ...] | None = None,
    token_cap: int = TOKEN_CAP,
    total_output_token_budget: int = 2560,
) -> dict[str, Any]:
    if not settings.llm_api_key or not settings.llm_model or not settings.llm_base_url:
        raise RuntimeError("LLM provider settings are incomplete; no request was sent")
    if not 1 <= token_cap <= TOKEN_CAP:
        raise ValueError(f"token cap must be between 1 and {TOKEN_CAP}")
    if (
        isinstance(total_output_token_budget, bool)
        or not isinstance(total_output_token_budget, int)
        or total_output_token_budget < 1
    ):
        raise ValueError("total output token budget must be a positive integer")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if case_ids is None:
        holdout = dataset.get("holdout_protocol", {})
        defaults = holdout.get("default_case_ids") if isinstance(holdout, dict) else None
        case_ids = tuple(defaults) if isinstance(defaults, list) else CASE_IDS
    holdout_separation = None
    is_holdout = _is_holdout_dataset(dataset.get("dataset_id"))
    enforce_single_exposure = dataset.get("dataset_id") == "advisor_multiturn_holdout_v7"
    if is_holdout:
        validate_dataset(dataset)
        if dataset.get("status") != "FROZEN_NOT_SCORED":
            raise ValueError("this holdout has already been exposed and cannot be rescored")
        references = load_holdout_references(dataset, cases_directory=dataset_path.parent)
        holdout_separation = validate_holdout_separation(dataset, references)
    if not case_ids or len(case_ids) > 20 or len(set(case_ids)) != len(case_ids):
        raise ValueError("select 1–20 distinct frozen case IDs per bounded run")
    requested_output_ceiling = len(case_ids) * 2 * len(VARIANTS) * token_cap
    request_ceiling = len(case_ids) * 2 * len(VARIANTS)
    if request_ceiling > 120:
        raise ValueError("requested provider calls exceed the approved 120-request evaluation cap")
    if requested_output_ceiling > total_output_token_budget:
        raise ValueError(
            f"requested output ceiling {requested_output_ceiling} exceeds total budget "
            f"{total_output_token_budget}"
        )
    by_id = {case["id"]: case for case in dataset["cases"]}
    if any(case_id not in by_id for case_id in case_ids):
        raise ValueError("the frozen multi-turn case selection is incomplete")
    selected = [by_id[case_id] for case_id in case_ids]
    for case in selected:
        agent_turns = [turn for turn in case["turns"] if turn["action_owner"] == "AGENT_DECISION"]
        if len(agent_turns) < 2:
            raise ValueError(f"{case['id']} does not contain two Agent-owned turns")
        if any(set(turn["allowed_tools"]) - set(TOOL_SCHEMAS) for turn in agent_turns[:2]):
            raise ValueError(f"{case['id']} has a tool without a read-only evaluation schema")

    exposure_marker = None
    if enforce_single_exposure:
        exposure_marker = _holdout_exposure_marker(dataset["dataset_id"])
        exposure_marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            with exposure_marker.open("x", encoding="utf-8") as handle:
                json.dump({
                    "dataset_id": dataset["dataset_id"],
                    "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                    "status": "EXPOSED_RUN_IN_PROGRESS",
                    "started_at_utc": datetime.now(UTC).isoformat(),
                }, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise ValueError("this holdout has already been exposed to a scoring run") from exc

    groups: dict[str, list[dict[str, Any]]] = {}
    for variant in VARIANTS:
        rows = []
        for case in selected:
            history: list[dict[str, Any]] = []
            turns = [turn for turn in case["turns"] if turn["action_owner"] == "AGENT_DECISION"][:2]
            case_rows = []
            for turn_index, turn in enumerate(turns):
                history.append({"role": "user", "content": turn["user"]})
                row = await _decide(
                    case=case,
                    turn_index=turn_index,
                    turn=turn,
                    variant=variant,
                    history=history,
                    token_cap=token_cap,
                )
                case_rows.append(row)
                action = row.get("action") or {}
                history.append(
                    {
                        "role": "assistant",
                        "action": action,
                        "summary": action.get("summary") or action.get("question") or "",
                    }
                )
                tool = action.get("tool") if action.get("kind") == "CALL_TOOL" else None
                if tool and _should_append_fixture_observation(row, turn["allowed_tools"]):
                    execution_observations = (row.get("execution") or {}).get("observations", {})
                    observation = execution_observations.get(tool) or _fixture_observation(
                        tool, case, action.get("arguments") or {},
                    )
                    history.append(
                        {
                            "role": "tool_observation",
                            "tool": tool,
                            "result": observation,
                        }
                    )
            matches = [row["expected_action_match"] for row in case_rows]
            task_scoreable = all(match is not None for match in matches)
            rows.append(
                {
                    "case_id": case["id"],
                    "initial_state": case.get("initial_state", {}),
                    "turns": case_rows,
                    "action_sequence_matches": all(matches) if task_scoreable else None,
                    "action_sequence_scoreable": task_scoreable,
                    "end_to_end_task_completion_evaluated": False,
                }
            )
        groups[variant] = rows

    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    summary = {}
    for name, cases in groups.items():
        turns = [turn for case in cases for turn in case["turns"]]
        scoreable_turns = [turn for turn in turns if turn["expected_action_match"] is not None]
        scoreable_sequences = [case for case in cases if case["action_sequence_scoreable"]]
        summary[name] = {
            "case_count": len(cases),
            "decision_count": len(turns),
            "scoreable_decision_count": len(scoreable_turns),
            "unscorable_decision_count": len(turns) - len(scoreable_turns),
            "expected_action_matches": sum(
                row["expected_action_match"] is True for row in scoreable_turns
            ),
            "action_sequence_scoreable_count": len(scoreable_sequences),
            "action_sequences_matched": sum(
                case["action_sequence_matches"] is True for case in scoreable_sequences
            ),
            "allowlist_violations": sum(not row["allowlist_valid"] for row in turns),
            "invalid_actions": sum(not row["action_valid"] for row in turns),
            "output_tokens": sum(row["output_tokens"] for row in turns),
            "prompt_tokens": sum(row["prompt_tokens"] for row in turns),
            "verified_tool_calls": sum(
                (row.get("execution") or {}).get("verified_tool_count", 0) for row in turns
            ),
            "safe_stops": sum(
                bool((row.get("execution") or {}).get("safe_stop")) for row in turns
            ),
            "runs": cases,
        }
    if exposure_marker is not None:
        exposure_marker.write_text(json.dumps({
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": digest,
            "status": "SCORED",
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "requests": request_ceiling,
            "requested_output_token_ceiling": requested_output_ceiling,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "evaluation_id": f"advisor_multiturn_live_{dataset['dataset_id']}",
        "provider_mode": "LIVE_MODEL_WITH_LOCAL_FIXTURE_OBSERVATIONS",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": digest,
        "source_provenance": evaluation_provenance(
            "advisor_multiturn_holdout_live" if is_holdout else "advisor_multiturn_live"
        ),
        "model": settings.llm_model,
        "base_url_host": settings.llm_base_url.split("/")[2]
        if "://" in settings.llm_base_url
        else None,
        "selected_case_ids": list(case_ids),
        "holdout_separation": holdout_separation,
        "requests": sum(group["decision_count"] for group in summary.values()),
        "per_request_output_token_cap": token_cap,
        "requested_output_token_ceiling": requested_output_ceiling,
        "total_output_token_budget": total_output_token_budget,
        "run_at_utc": datetime.now(UTC).isoformat(),
        "summary": summary,
        "groups": groups,
        "arm_definitions": {
            "B2_AGENT_LOOP": "The model selects an action from the supplied observation and allowlisted tool schemas.",
            "B3_VERIFIED_RECOVERY_LOOP": (
                "Same model decision is executed by production run_constrained_loop with the production "
                "search, weather, matrix and route verifiers; evaluation-local fixtures exercise bounded safe-stop recovery."
            ),
        },
        "scope": f"{len(case_ids)} frozen two-turn tasks per variant. Real model selects each action; local fixture observations carry tool results across turns. Reports adjudicated action correctness and action-sequence consistency separately from B3 tool verification/safe stops. It does not evaluate end-to-end task completion, full answer quality, real providers, DB persistence, or formal writes.",
        "limitations": [
            (
                "Newly frozen, case-ID-disjoint action-selection holdout; it is not a full product API or independent human quality evaluation."
                if is_holdout
                else "Small selected subset of the frozen 60-case set; not held out from policy design."
            ),
            "Tool observations are deterministic local fixtures, not live map/weather calls.",
            "The B3 arm does not exercise database-backed interrupted-action checkpoint restore or the full Advisor HTTP workflow.",
            "No B1 live model arm; compare with the separate fixed-workflow and scripted evaluations.",
            "Provider-reported token usage may be absent or inaccurate.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="Send bounded live model requests")
    parser.add_argument(
        "--dataset", type=Path, default=DATASET, help="Frozen contract dataset to evaluate"
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Frozen case ID to include; repeat up to twenty times (default: dataset selection)",
    )
    parser.add_argument(
        "--output-token-cap",
        type=int,
        default=TOKEN_CAP,
        help=f"Per-request output ceiling, 1–{TOKEN_CAP}",
    )
    parser.add_argument(
        "--total-output-token-budget",
        type=int,
        default=2560,
        help="Total requested output-token ceiling across both variants",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.run_live:
        raise SystemExit("Pass --run-live to explicitly execute a bounded evaluation")
    selected_case_ids = tuple(args.case_ids) if args.case_ids else None
    report = asyncio.run(
        run_live(
            dataset_path=args.dataset,
            case_ids=selected_case_ids,
            token_cap=args.output_token_cap,
            total_output_token_budget=args.total_output_token_budget,
        )
    )
    output_path = args.output or (
        ROOT / "tests/evaluation/results"
        / f"advisor_multiturn_live_{report['dataset_id']}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        if _is_holdout_dataset(report["dataset_id"])
        else RESULT
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "requests": report["requests"],
                "requested_output_token_ceiling": report["requested_output_token_ceiling"],
                "summary": {
                    key: {name: value for name, value in group.items() if name != "runs"}
                    for key, group in report["summary"].items()
                },
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
