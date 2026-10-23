"""Small-budget live action evaluation; refuses to run without an explicit request cap."""

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

from tests.evaluation.advisor_fixed_workflow_baseline import (
    ROOT,
    _candidates,
    _matrix_for_state,
    build_schedule_fixture,
)
from tests.evaluation.advisor_fixed_workflow_baseline import (
    evaluate_case as evaluate_fixed_case,
)
from tests.evaluation.provenance import evaluation_provenance

DATASET = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v3.json"
MAX_AUTHORIZED_REQUESTS = 6
MAX_OUTPUT_TOKENS_PER_REQUEST = 128
MAX_AUTHORIZED_OUTPUT_TOKENS = 960


class EmptyInput(BaseModel):
    pass


def validate_live_limits(request_limit: int, output_token_cap: int) -> None:
    if not 1 <= request_limit <= MAX_AUTHORIZED_REQUESTS:
        raise ValueError(f"request limit must be between 1 and {MAX_AUTHORIZED_REQUESTS}")
    if not 1 <= output_token_cap <= MAX_OUTPUT_TOKENS_PER_REQUEST:
        raise ValueError(
            f"this runner caps output at {MAX_OUTPUT_TOKENS_PER_REQUEST} tokens per request"
        )
    if request_limit * output_token_cap > MAX_AUTHORIZED_OUTPUT_TOKENS:
        raise ValueError("aggregate requested output-token cap exceeds 960")


def _safe_tool_schemas() -> dict[str, dict[str, Any]]:
    schema = EmptyInput.model_json_schema()
    return {
        "get_distance_matrix": {"input_schema": schema},
        "build_schedule_draft": {"input_schema": schema},
    }


def _decision_limit(group: str, state: dict[str, Any]) -> int:
    if state.get("route_matrix") == "provider_timeout":
        return 2
    if state.get("route_matrix") == "malformed":
        return 2
    return 2


async def _run_agent_case(
    group: str,
    case: dict[str, Any],
    *,
    api_key: str,
    model: str,
    base_url: str,
    request_limit: int,
    output_token_cap: int,
) -> dict[str, Any]:
    state = case["state"]
    route_failed = False
    route_verified = False
    route_matrix_rejected = False
    matrix_holder: list[list[int] | None] = [None]
    request_count = 0
    input_tokens = 0
    output_tokens = 0
    token_usage_unreported = False
    budget_exhausted = False
    reported_token_cap_exceeded = False
    action_records: list[dict[str, str]] = []

    async def get_matrix(_args: EmptyInput) -> dict[str, Any]:
        nonlocal route_failed, route_verified
        if state.get("route_matrix") == "provider_timeout":
            route_failed = True
            raise ToolFailure("MAP_TIMEOUT")
        candidates = _candidates(case["id"], state, int(state.get("saved_places", 0)))
        matrix_holder[0] = _matrix_for_state(state, len(candidates))
        # The raw provider wrapper labels its payload as returned data. The
        # B3 verifier independently checks dimensions and numeric values.
        route_verified = True
        return {"status": "VERIFIED", "durations_min": matrix_holder[0]}

    async def build_draft(_args: EmptyInput) -> dict[str, Any]:
        proposal = build_schedule_fixture(case["id"], state, matrix_holder[0])
        proposal["route_warning"] = route_failed or (
            int(state.get("saved_places", 0)) > 1 and matrix_holder[0] is None
        )
        return proposal

    runtime = AgentRuntime(
        [
            ToolSpec("get_distance_matrix", EmptyInput, get_matrix, retry_limit=0),
            ToolSpec("build_schedule_draft", EmptyInput, build_draft, retry_limit=0),
        ],
        lambda _entry: asyncio.sleep(0),
        max_steps=3,
        max_attempts=3,
        token_budget=request_limit * output_token_cap,
    )

    trip_context = {
        "destination": state.get("destination", "测试目的地"),
        "start_date": state.get("trip_dates", [None, None])[0],
        "end_date": state.get("trip_dates", [None, None])[-1],
        "pace_level": state.get("pace_level"),
        "saved_place_count": int(state.get("saved_places", 0)),
        "fixed_stop_count": len(state.get("fixed_stops", []))
        if isinstance(state.get("fixed_stops"), list)
        else int(state.get("fixed_stops", 0) or 0),
        "budget_status": state.get("budget_status"),
    }
    observation: dict[str, Any] = {
        "task": "schedule_existing_trip_places",
        "user_goal": case["user_goal"],
        "trip_context": trip_context,
        "route_evidence_status": "absent",
        "policy": "Never commit changes. Ask when required facts are missing. Preserve all selected places and fixed appointments.",
    }
    tool_schemas = _safe_tool_schemas()
    decisions_remaining = _decision_limit(group, state)

    async def policy(obs: dict[str, Any], allowed: list[str]) -> AgentAction:
        nonlocal request_count, input_tokens, output_tokens
        nonlocal token_usage_unreported, budget_exhausted, reported_token_cap_exceeded
        if (
            group == "B3_VERIFIER_RECOVERY"
            and "get_distance_matrix_verification_failure" in obs
            and "build_schedule_draft" in allowed
        ):
            action_records.append({"kind": "SYSTEM_RECOVERY", "tool": "build_schedule_draft"})
            return AgentAction(
                kind="CALL_TOOL", tool="build_schedule_draft", arguments={},
                summary="路线矩阵验证失败，使用本地估算生成带警告草案",
            )
        available = {
            name: tool_schemas[name]
            for name in allowed
            if name in tool_schemas and not (
                name == "get_distance_matrix"
                and any(
                    key in obs
                    for key in (
                        "get_distance_matrix",
                        "get_distance_matrix_failure",
                        "get_distance_matrix_verification_failure",
                    )
                )
            )
        }
        if request_count >= request_limit:
            budget_exhausted = True
            return AgentAction(kind="FINISH", summary="live evaluation request cap reached")
        if output_tokens > request_count * output_token_cap:
            budget_exhausted = True
            reported_token_cap_exceeded = True
            return AgentAction(kind="FINISH", summary="provider usage exceeded the requested token cap")
        request_count += 1
        try:
            action, usage = await decide_next_action(
                {
                    **obs,
                    "tool_result_policy": "A route lookup may fail. If it fails, do not repeat it; either propose a clearly warned local estimate or stop and explain.",
                },
                available,
                api_key=api_key,
                model=model,
                base_url=base_url,
                token_budget=output_token_cap,
            )
            input_tokens += usage.prompt_tokens
            output_tokens += usage.completion_tokens
            if usage.total_tokens == 0:
                token_usage_unreported = True
            if usage.completion_tokens > output_token_cap:
                reported_token_cap_exceeded = True
            action_records.append({"kind": action.kind, "tool": action.tool or ""})
            return action
        except PlannerDecisionFailure as exc:
            input_tokens += exc.usage.prompt_tokens
            output_tokens += exc.usage.completion_tokens
            if exc.usage.total_tokens == 0:
                token_usage_unreported = True
            if exc.usage.completion_tokens > output_token_cap:
                reported_token_cap_exceeded = True
            action_records.append({"kind": "INVALID_MODEL_ACTION", "tool": ""})
            return AgentAction(kind="FINISH", summary="model action failed schema validation")
        except ToolFailure as exc:
            action_records.append({"kind": "MODEL_CALL_FAILED", "tool": exc.code})
            return AgentAction(kind="FINISH", summary="model request failed; stop without writing")

    async def verifier(tool: str, result: dict[str, Any]) -> bool:
        nonlocal route_matrix_rejected, route_verified
        if group == "B2_AGENT_LOOP":
            return True
        if tool == "get_distance_matrix":
            matrix = result.get("durations_min")
            valid = verify_distance_matrix(
                {"durations_min": matrix}, int(state.get("saved_places", 0))
            )
            route_matrix_rejected = not valid
            route_verified = valid
            if not valid:
                matrix_holder[0] = None
            return valid
        if tool == "build_schedule_draft":
            expected = int(state.get("saved_places", 0))
            candidates = result.get("candidate_ids")
            return (
                result.get("all_selected_places_accounted_for") is True
                and isinstance(candidates, list)
                and len(candidates) == expected
                and (
                    expected <= 1
                    or route_verified
                    or route_failed and result.get("route_warning") is True
                    or route_matrix_rejected and result.get("route_warning") is True
                )
            )
        return False

    loop = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=lambda _state: asyncio.sleep(0),
        initial_observation=observation,
        max_decisions=decisions_remaining,
        allowed_tool_names=set(runtime.tools),
        continue_on_tool_failure=group == "B3_VERIFIER_RECOVERY",
        continue_on_verifier_failure=group == "B3_VERIFIER_RECOVERY",
    )
    proposal = loop.observation.get("build_schedule_draft")
    final_state = (
        "NEEDS_USER_INPUT" if loop.pending_question
        else "DRAFT_READY" if proposal and proposal.get("feasible")
        else "SAFE_STOP" if loop.status in {"FAILED", "REJECTED", "FALLBACK", "SUCCEEDED"}
        else loop.status
    )
    malformed_matrix_attempted = any(
        call.get("tool") == "get_distance_matrix" and call.get("status") == "SUCCEEDED"
        for call in runtime.calls
    )
    hard_invariants = {
        "no_formal_write": True,
        "no_repeated_failed_route_call": sum(
            1 for call in runtime.calls
            if call.get("tool") == "get_distance_matrix" and call.get("status") == "FAILED"
        ) <= 1,
        "malformed_route_not_accepted_as_verified": state.get("route_matrix") != "malformed"
        or not malformed_matrix_attempted
        or route_matrix_rejected,
    }
    return {
        "case_id": case["id"],
        # Keep orchestration termination separate from the user-visible outcome:
        # the decision budget may end immediately after a valid draft is built.
        "loop_status": loop.status,
        "state": final_state,
        "final_state": final_state,
        "actions": action_records,
        "tool_attempts": [
            {
                "tool": call.get("tool"),
                "status": call.get("status"),
                "error_code": call.get("error_code"),
            }
            for call in runtime.calls
        ],
        "request_count": request_count,
        "input_tokens_reported": input_tokens,
        "output_tokens_reported": output_tokens,
        "token_usage_unreported": token_usage_unreported,
        "request_cap_reached": budget_exhausted,
        "reported_token_cap_exceeded": reported_token_cap_exceeded,
        "hard_invariants": hard_invariants,
        "route_matrix_rejected": route_matrix_rejected,
        "draft_summary": {
            "feasible": bool(proposal and proposal.get("feasible")),
            "candidate_count": len(proposal.get("candidate_ids", [])) if proposal else 0,
            "route_warning": bool(proposal and proposal.get("route_warning")),
        },
    }


async def run_live_eval(
    *, api_key: str, model: str, base_url: str, request_limit: int, output_token_cap: int,
) -> dict[str, Any]:
    validate_live_limits(request_limit, output_token_cap)
    if not api_key.strip() or not model.strip():
        raise ValueError("LLM_API_KEY and LLM_MODEL must be configured")
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases_by_family = {
        case["scenario_family"]: case for case in dataset["cases"]
        if case.get("scenario_family") in {
            "route-provider-failure-adapts-with-warning",
            "malformed_route_matrix",
        }
    }
    selected_cases = [
        cases_by_family["route-provider-failure-adapts-with-warning"],
        cases_by_family["malformed_route_matrix"],
    ]
    digest = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    groups: dict[str, list[dict[str, Any]]] = {
        "B1_FIXED": [
            {"case_id": case["id"], **await evaluate_fixed_case(case)}
            for case in selected_cases
        ],
        "B2_AGENT_LOOP": [],
        "B3_VERIFIER_RECOVERY": [],
    }
    requests_used = 0
    stopped_for_provider_token_overrun = False
    for case in selected_cases:
        for group in ("B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"):
            remaining = request_limit - requests_used
            if remaining <= 0:
                break
            row = await _run_agent_case(
                group,
                case,
                api_key=api_key,
                model=model,
                base_url=base_url,
                request_limit=remaining,
                output_token_cap=output_token_cap,
            )
            groups[group].append(row)
            requests_used += row["request_count"]
            if row["reported_token_cap_exceeded"]:
                stopped_for_provider_token_overrun = True
                break
        if stopped_for_provider_token_overrun:
            break
        if requests_used >= request_limit:
            break
    groups_complete = {
        group: group == "B1_FIXED" or len(rows) == len(selected_cases)
        for group, rows in groups.items()
    }
    live_rows = [
        row for group, rows in groups.items() if group != "B1_FIXED" for row in rows
    ]
    request_caps_reached = any(row["request_cap_reached"] for row in live_rows)
    reported_token_cap_exceeded = any(
        row["reported_token_cap_exceeded"] for row in live_rows
    )
    model_action_failures = sum(
        action.get("kind") == "INVALID_MODEL_ACTION"
        for row in live_rows for action in row["actions"]
    )
    draft_ready_cases = sum(row["final_state"] == "DRAFT_READY" for row in live_rows)
    return {
        "evaluation_id": "advisor_live_action_smoke_v1",
        "provider_mode": "LIVE_MODEL_WITH_SCRIPTED_TOOLS",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": digest,
        "source_provenance": evaluation_provenance("advisor_live_action"),
        "model": model,
        "base_url_origin": base_url.split("/v1", 1)[0],
        "request_limit": request_limit,
        "output_token_cap_per_request": output_token_cap,
        "aggregate_requested_output_cap": request_limit * output_token_cap,
        "requests_used": requests_used,
        "groups_complete": groups_complete,
        "request_caps_reached": request_caps_reached,
        "reported_token_cap_exceeded": reported_token_cap_exceeded,
        "model_action_failures": model_action_failures,
        "draft_ready_cases": draft_ready_cases,
        "all_model_actions_valid": model_action_failures == 0,
        "run_complete": (
            all(groups_complete.values())
            and not request_caps_reached
            and not reported_token_cap_exceeded
        ),
        "groups": groups,
        "limitations": [
            "Exploratory smoke comparison of two frozen task families; not a full 40-case benchmark.",
            "B1 replays the committed fixed Advisor workflow; B2/B3 call the configured model.",
            "B1/B2/B3 share the production build_itinerary planner and deterministic route fixtures; no TravelPilot data is written.",
            "Provider-reported token usage may be absent or inaccurate.",
        ],
        "run_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-live-requests", type=int, required=True,
        help="Explicit per-run ceiling; hard maximum is six requests.",
    )
    parser.add_argument(
        "--output-token-cap", type=int, default=128,
        help="Maximum requested completion tokens per request; runner hard-caps this at 128.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_live_limits(args.authorize_live_requests, args.output_token_cap)
    except ValueError as exc:
        parser.error(str(exc))
    from travelpilot_api.settings import settings

    report = asyncio.run(run_live_eval(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        request_limit=args.authorize_live_requests,
        output_token_cap=args.output_token_cap,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "evaluation_id": report["evaluation_id"],
        "requests_used": report["requests_used"],
        "aggregate_requested_output_cap": report["aggregate_requested_output_cap"],
        "report": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
