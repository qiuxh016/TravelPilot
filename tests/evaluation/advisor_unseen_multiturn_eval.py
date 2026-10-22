"""Matched unseen-task pilot using the production constrained AgentRuntime.

B1 is the existing scripted fixed-workflow baseline. B2 runs the live model
inside AgentRuntime without output verification/recovery. B3 uses the same
model/prompt path with the production runtime verifier and bounded recovery.
All provider observations are synthetic fixtures; no trip data is written.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, run_constrained_loop
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

from tests.evaluation import advisor_multiturn_live_eval as action_eval
from tests.evaluation.advisor_multiturn_contracts import (
    load_holdout_references,
    validate_dataset,
    validate_holdout_separation,
)
from tests.evaluation.advisor_multiturn_task_eval import evaluate_dataset as evaluate_scripted
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_holdout_v3.json"
DEFAULT_OUTPUT_CAP = 128
DEFAULT_REQUEST_CAP = 120
DEFAULT_TOTAL_OUTPUT_CAP = 15_360
LIVE_VARIANTS = ("B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY")


class _EmptyInput(BaseModel):
    pass


async def _noop(_value: dict[str, Any]) -> None:
    return None


def _fixture_tools(case: dict[str, Any]) -> list[ToolSpec]:
    state = case.get("initial_state", {})

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_trip_weather" and state.get("weather_provider") == "timeout":
            raise ToolFailure("WEATHER_TIMEOUT")
        if (
            name in {"get_trip_route", "get_distance_matrix"}
            and state.get("route_provider") == "timeout"
        ):
            raise ToolFailure("MAP_TIMEOUT")
        if name == "get_distance_matrix" and state.get("route_matrix") == "malformed":
            return {
                "status": "VERIFIED_FIXTURE",
                "source": "LOCAL_FIXTURE",
                "durations_min": [[0, -1]],
            }
        result = action_eval._fixture_observation(name, case)
        result.setdefault("source", "LOCAL_FIXTURE")
        return result

    class _SearchInput(BaseModel):
        keyword: str
        city: str | None = None

    class _MatrixInput(BaseModel):
        points: list[tuple[float, float]]

    # RouteQueryInput is already used by the production evaluator's schema
    # validator; re-use the exact production input contract here.
    route_model = action_eval.RouteQueryInput

    async def handler(name: str, args: BaseModel) -> dict[str, Any]:
        return await execute(name, args.model_dump(mode="json"))

    async def search(args: BaseModel) -> dict[str, Any]:
        return await handler("search_places", args)

    async def matrix(args: BaseModel) -> dict[str, Any]:
        return await handler("get_distance_matrix", args)

    async def weather(args: BaseModel) -> dict[str, Any]:
        return await handler("get_trip_weather", args)

    async def route(args: BaseModel) -> dict[str, Any]:
        return await handler("get_trip_route", args)

    async def draft(args: BaseModel) -> dict[str, Any]:
        return await handler("build_schedule_draft", args)

    return [
        ToolSpec("search_places", _SearchInput, search, retry_limit=0),
        ToolSpec("get_trip_weather", _EmptyInput, weather, retry_limit=0),
        ToolSpec("get_distance_matrix", _MatrixInput, matrix, retry_limit=0),
        ToolSpec("get_trip_route", route_model, route, retry_limit=0),
        ToolSpec("build_schedule_draft", _EmptyInput, draft, retry_limit=0),
    ]


def _expected_match(turn: dict[str, Any], action: dict[str, Any]) -> bool | None:
    return action_eval._expected_match(turn, action)


def _verified_fixture(name: str, result: dict[str, Any], case: dict[str, Any]) -> bool:
    """Run deterministic evidence checks; never accept provider labels alone."""
    if result.get("source") != "LOCAL_FIXTURE":
        return False
    if name == "get_distance_matrix":
        expected_size = action_eval._expected_schedule_stop_count(case)
        return (
            result.get("status") in {"PROVIDER_RETURNED", "VERIFIED_FIXTURE"}
            and result.get("point_count") == expected_size
            and verify_distance_matrix(
                {"durations_min": result.get("durations_min")},
                expected_size,
            )
        )
    if name == "search_places":
        return result.get("status") == "VERIFIED_FIXTURE" and isinstance(result.get("places"), list)
    if name == "get_trip_weather":
        return result.get("status") == "AVAILABLE_FIXTURE" and isinstance(result.get("days"), list)
    if name == "get_trip_route":
        return (
            result.get("status") == "VERIFIED_FIXTURE"
            and isinstance(
                result.get("duration_min"),
                (int, float),
            )
            and result.get("duration_min") >= 0
        )
    if name == "build_schedule_draft":
        return result.get("status") == "DRAFT_FIXTURE" and result.get("formal_write") is False
    return False


def _safe_action(action: AgentAction) -> dict[str, Any]:
    return action.model_dump(mode="json")


def _validate_fixture_evidence(dataset: dict[str, Any]) -> None:
    """Fail before spending model calls when a tool fixture lacks grounding."""
    for case in dataset.get("cases", []):
        if not any(
            "get_trip_route" in turn.get("allowed_tools", [])
            for turn in case.get("turns", [])
        ):
            continue
        places = case.get("initial_state", {}).get("known_places")
        ids = {
            place.get("id")
            for place in places
            if isinstance(place, dict) and isinstance(place.get("id"), str) and place["id"]
        } if isinstance(places, list) else set()
        if len(ids) < 2:
            raise ValueError(
                f"{case.get('id')}: route evaluation requires at least two grounded known_places IDs"
            )


async def _run_turn(
    *,
    case: dict[str, Any],
    turn: dict[str, Any],
    turn_index: int,
    arm: str,
    history: list[dict[str, Any]],
    api_key: str,
    model: str,
    base_url: str,
    output_token_cap: int,
    global_budget: dict[str, int],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    usage_rows: list[dict[str, int]] = []
    fixture_observations: list[dict[str, Any]] = []
    verification_failures = 0
    invalid_actions = 0
    runtime = AgentRuntime(
        _fixture_tools(case),
        _noop,
        max_steps=3,
        max_attempts=3,
        token_budget=output_token_cap * 2,
    )
    observation: dict[str, Any] = {
        "task": case["task_type"],
        "user_goal": case["turns"][0]["user"],
        "current_user_message": turn["user"],
        "turn_number": turn_index + 1,
        "trip_context": case.get("initial_state", {}),
        "conversation_history": history[-8:],
        "tool_observations_are_local_fixtures": True,
        "policy": (
            "Use only allowlisted read-only tools. Preserve explicit dates, fixed events, hard constraints, "
            "meal/lodging scope, and all previously selected items. Ask only a focused necessary question. "
            "Never claim a booking or write a formal plan."
        ),
    }

    async def policy(obs: dict[str, Any], allowed_names: list[str]) -> AgentAction:
        nonlocal verification_failures, invalid_actions
        # A successful first action completes this user turn. Only B3 gets a
        # second model decision after an actual tool or verifier failure.
        failed = any(key.endswith(("_failure", "_verification_failure")) for key in obs)
        if actions and (arm != "B3_VERIFIER_RECOVERY" or not failed):
            return AgentAction(kind="FINISH", summary="本轮动作已执行")
        if global_budget["requests"] >= global_budget["request_cap"]:
            global_budget["exhausted"] = 1
            return AgentAction(kind="FINISH", summary="评测请求额度已用完")
        allowed_for_turn = set(turn["allowed_tools"]) & set(action_eval.TOOL_SCHEMAS)
        if arm == "B3_VERIFIER_RECOVERY":
            failed_tools = {
                key.removesuffix("_verification_failure").removesuffix("_failure")
                for key in obs
                if key.endswith(("_failure", "_verification_failure"))
            }
            allowed_for_turn.difference_update(failed_tools)
        matrix_observation = next(
            (
                item.get("result")
                for item in reversed([*history, *fixture_observations])
                if item.get("role") == "tool_observation"
                and item.get("tool") == "get_distance_matrix"
            ),
            None,
        )
        if "build_schedule_draft" in allowed_for_turn:
            expected_count = action_eval._expected_schedule_stop_count(case)
            if (
                expected_count > 1
                and not isinstance(matrix_observation, dict)
                or arm == "B3_VERIFIER_RECOVERY"
                and expected_count > 1
                and not _verified_fixture(
                    "get_distance_matrix",
                    matrix_observation or {},
                    case,
                )
            ):
                allowed_for_turn.discard("build_schedule_draft")
        schemas = {
            name: action_eval.TOOL_SCHEMAS[name]
            for name in sorted(allowed_for_turn & set(allowed_names))
        }
        model_observation = {
            **obs,
            "conversation_history": [*history[-8:], *fixture_observations],
            "available_tools": sorted(schemas),
        }
        reserved = runtime.reserve_output_tokens(output_token_cap)
        global_budget["requests"] += 1
        try:
            decision, usage = await decide_next_action(
                model_observation,
                schemas,
                api_key=api_key,
                model=model,
                base_url=base_url,
                token_budget=reserved,
            )
            runtime.record_token_usage(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                reserved=reserved,
                total_tokens=usage.total_tokens,
            )
            global_budget["output_tokens"] += usage.completion_tokens
            if usage.completion_tokens > output_token_cap:
                global_budget["reported_overrun"] = 1
            usage_rows.append(
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "output_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }
            )
            serialized = _safe_action(decision)
            if decision.kind == "CALL_TOOL":
                arguments_valid, _ = action_eval._validate_tool_arguments(
                    decision.tool,
                    decision.arguments,
                    case.get("initial_state", {}),
                )
                if decision.tool not in schemas or not arguments_valid:
                    invalid_actions += 1
                    actions.append(
                        {
                            "kind": "INVALID_MODEL_ACTION",
                            "tool": decision.tool,
                            "summary": "tool not allowed for this turn or arguments lack evidence",
                        }
                    )
                    return AgentAction(kind="FINISH", summary="动作或参数未通过边界校验，安全停止")
            actions.append(serialized)
            return decision
        except PlannerDecisionFailure as exc:
            runtime.record_token_usage(
                input_tokens=exc.usage.prompt_tokens,
                output_tokens=exc.usage.completion_tokens,
                reserved=reserved,
                total_tokens=exc.usage.total_tokens,
            )
            global_budget["output_tokens"] += exc.usage.completion_tokens
            if exc.usage.completion_tokens > output_token_cap:
                global_budget["reported_overrun"] = 1
            usage_rows.append(
                {
                    "prompt_tokens": exc.usage.prompt_tokens,
                    "output_tokens": exc.usage.completion_tokens,
                    "total_tokens": exc.usage.total_tokens,
                }
            )
            invalid_actions += 1
            actions.append(
                {
                    "kind": "INVALID_MODEL_ACTION",
                    "summary": exc.code,
                    "error_category": exc.reason or "UNCLASSIFIED",
                }
            )
            return AgentAction(kind="FINISH", summary="模型动作不符合约定，安全停止")
        except BaseException:
            runtime.release_output_tokens(reserved)
            raise

    async def verifier(name: str, result: dict[str, Any]) -> bool:
        nonlocal verification_failures
        fixture_observations.append({"role": "tool_observation", "tool": name, "result": result})
        valid = _verified_fixture(name, result, case) if arm == "B3_VERIFIER_RECOVERY" else True
        if not valid:
            verification_failures += 1
            fixture_observations[-1]["verification"] = "REJECTED"
        else:
            fixture_observations[-1]["verification"] = "VERIFIED"
        return valid

    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=_noop,
        initial_observation=observation,
        max_decisions=2 if arm == "B3_VERIFIER_RECOVERY" else 1,
        allowed_tool_names=set(runtime.tools),
        continue_on_tool_failure=arm == "B3_VERIFIER_RECOVERY",
        continue_on_verifier_failure=arm == "B3_VERIFIER_RECOVERY",
    )
    first_action = actions[0] if actions else {}
    return {
        "case_id": case["id"],
        "turn": turn_index + 1,
        "expected_action": turn["expected_action"],
        "action": first_action,
        "expected_action_match": _expected_match(turn, first_action),
        "additional_recovery_actions": actions[1:],
        "tool_transitions": result.transitions,
        "loop_status": result.status,
        "loop_error": result.error,
        "verification_failures": verification_failures,
        "invalid_model_actions": invalid_actions,
        "tool_observations": fixture_observations,
        "output_usage": usage_rows,
        "formal_write": False,
    }


async def run_live(
    *,
    dataset_path: Path = DATASET,
    api_key: str,
    model: str,
    base_url: str,
    request_cap: int = DEFAULT_REQUEST_CAP,
    output_token_cap: int = DEFAULT_OUTPUT_CAP,
    total_output_token_cap: int = DEFAULT_TOTAL_OUTPUT_CAP,
) -> dict[str, Any]:
    if not api_key.strip() or not model.strip() or not base_url.strip():
        raise ValueError("LLM provider configuration is incomplete")
    if not 1 <= request_cap <= DEFAULT_REQUEST_CAP:
        raise ValueError(f"request cap must be 1–{DEFAULT_REQUEST_CAP}")
    if not 1 <= output_token_cap <= DEFAULT_OUTPUT_CAP:
        raise ValueError(f"per-request output cap must be 1–{DEFAULT_OUTPUT_CAP}")
    if request_cap * output_token_cap > total_output_token_cap:
        raise ValueError("aggregate requested output ceiling exceeds the authorized token budget")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    contract_report = validate_dataset(dataset)
    _validate_fixture_evidence(dataset)
    reference_sets = load_holdout_references(dataset, cases_directory=dataset_path.parent)
    separation = validate_holdout_separation(dataset, reference_sets)
    cases = dataset["cases"]
    if len(cases) != 20:
        raise ValueError("the pilot must contain exactly 20 frozen tasks")
    scripted = evaluate_scripted(dataset)
    budget = {
        "requests": 0,
        "output_tokens": 0,
        "request_cap": request_cap,
        "exhausted": 0,
        "reported_overrun": 0,
    }
    groups: dict[str, list[dict[str, Any]]] = {arm: [] for arm in LIVE_VARIANTS}
    for arm in LIVE_VARIANTS:
        for case in cases:
            history: list[dict[str, Any]] = []
            turns_report = []
            for index, turn in enumerate(case["turns"]):
                if budget["exhausted"] or budget["reported_overrun"]:
                    break
                history.append({"role": "user", "content": turn["user"]})
                row = await _run_turn(
                    case=case,
                    turn=turn,
                    turn_index=index,
                    arm=arm,
                    history=history,
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    output_token_cap=output_token_cap,
                    global_budget=budget,
                )
                turns_report.append(row)
                action = row.get("action") or {}
                history.append(
                    {
                        "role": "assistant",
                        "action": action,
                        "content": action.get("question") or action.get("summary") or "",
                    }
                )
                history.extend(row["tool_observations"])
            matches = [row["expected_action_match"] for row in turns_report]
            scoreable = len(turns_report) == len(case["turns"]) and all(
                value is not None for value in matches
            )
            groups[arm].append(
                {
                    "case_id": case["id"],
                    "family": case["family"],
                    "turns": turns_report,
                    "task_scoreable": scoreable,
                    "task_all_turns_matched": all(matches) if scoreable else None,
                }
            )
            if budget["exhausted"] or budget["reported_overrun"]:
                break
    summaries = {}
    for arm, rows in groups.items():
        turns = [turn for case in rows for turn in case["turns"]]
        scored = [row for row in turns if row.get("expected_action_match") is not None]
        summaries[arm] = {
            "case_count_completed": sum(len(row["turns"]) == 2 for row in rows),
            "decision_count": len(turns),
            "action_matches": sum(row["expected_action_match"] is True for row in scored),
            "scoreable_decisions": len(scored),
            "complete_task_matches": sum(row["task_all_turns_matched"] is True for row in rows),
            "hard_safety_violations": sum(
                item.get("loop_error") == "TOOL_NOT_ALLOWLISTED" or item.get("formal_write") is True
                for item in turns
            ),
            "verification_failures": sum(item.get("verification_failures", 0) for item in turns),
            "invalid_model_actions": sum(item.get("invalid_model_actions", 0) for item in turns),
            "invalid_action_categories": {
                category: sum(
                    (item.get("action") or {}).get("error_category") == category for item in turns
                )
                for category in sorted(
                    {
                        (item.get("action") or {}).get("error_category", "UNCLASSIFIED")
                        for item in turns
                        if (item.get("action") or {}).get("kind") == "INVALID_MODEL_ACTION"
                    }
                )
            },
            "tool_failures": sum(
                transition.get("status") == "FAILED"
                for item in turns
                for transition in item.get("tool_transitions", [])
            ),
            "verified_tool_actions": sum(
                transition.get("status") == "VERIFIED"
                for item in turns
                for transition in item.get("tool_transitions", [])
            ),
            "recovery_decisions": sum(
                len(item.get("additional_recovery_actions", [])) for item in turns
            ),
            "loop_status_counts": {
                status: sum(item.get("loop_status") == status for item in turns)
                for status in sorted({item.get("loop_status") for item in turns})
            },
            "output_tokens": sum(
                usage.get("output_tokens", 0)
                for item in turns
                for usage in item.get("output_usage", [])
            ),
        }
    return {
        "evaluation_id": f"advisor_unseen_multiturn_pilot_{dataset.get('version', 'unknown')}",
        "provider_mode": "LIVE_MODEL_WITH_PRODUCTION_RUNTIME_AND_SYNTHETIC_TOOLS",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "dataset_contract": contract_report,
        "holdout_separation": separation,
        "source_provenance": evaluation_provenance("advisor_unseen_multiturn_pilot"),
        "model": model,
        "base_url_host": base_url.split("/")[2] if "://" in base_url else None,
        "request_cap": request_cap,
        "requests_used": budget["requests"],
        "output_token_cap_per_request": output_token_cap,
        "aggregate_requested_output_ceiling": request_cap * output_token_cap,
        "output_tokens_reported": budget["output_tokens"],
        "request_budget_exhausted": bool(budget["exhausted"]),
        "provider_reported_token_overrun": bool(budget["reported_overrun"]),
        "fixed_workflow_baseline": scripted["groups"]["B1_FIXED_POLICY"],
        "scripted_policy_diagnostics": scripted["groups"],
        "summary": summaries,
        "groups": groups,
        "arm_definitions": {
            "B1_FIXED_POLICY": "Offline fixed-workflow heuristic replay; not a live-model result.",
            "B2_AGENT_LOOP": "Live model action selection executed by the production AgentRuntime; output verifier bypassed and failures stop the turn.",
            "B3_VERIFIER_RECOVERY": "Same live model action selection and AgentRuntime with deterministic evidence verification and bounded re-decision after tool/verifier failure.",
        },
        "limitations": [
            "20 cases are an exploratory pilot, not statistical proof of arbitrary-trip competence.",
            "Exact prompt and case-ID separation is checked; semantic independence is not established.",
            "B1 is an offline synthetic approximation; do not compare it as if it were a live-model arm.",
            "Model action summaries/questions are not a full product-response or independent-user-utility evaluation.",
            "Tool results are local synthetic fixtures. No maps/weather provider or TravelPilot trip data is accessed.",
            "A provider may under-report token usage; the runner still enforces requested caps before each call.",
        ],
        "run_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-live", action="store_true", help="Run the explicitly approved live-model pilot"
    )
    parser.add_argument("--request-cap", type=int, default=DEFAULT_REQUEST_CAP)
    parser.add_argument("--output-token-cap", type=int, default=DEFAULT_OUTPUT_CAP)
    parser.add_argument("--total-output-token-cap", type=int, default=DEFAULT_TOTAL_OUTPUT_CAP)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required")
    from travelpilot_api.settings import settings

    report = asyncio.run(
        run_live(
            dataset_path=args.dataset,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            request_cap=args.request_cap,
            output_token_cap=args.output_token_cap,
            total_output_token_cap=args.total_output_token_cap,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "evaluation_id": report["evaluation_id"],
                "requests_used": report["requests_used"],
                "output_tokens_reported": report["output_tokens_reported"],
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
