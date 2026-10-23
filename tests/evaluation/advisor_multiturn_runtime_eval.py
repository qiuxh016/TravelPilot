"""Run frozen multi-turn Agent decisions through the production constrained loop.

This uses a deterministic policy and local fixture tools. It validates action
contracts, runtime allowlists, tool execution and lifecycle checkpoints; it is
not an LLM quality score or full HTTP/service integration test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from travelpilot_core.agents.loop import AgentAction, AgentTaskContext, run_constrained_loop
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.ports.tools import ToolFailure, ToolSpec

from tests.evaluation.advisor_multiturn_contracts import validate_dataset
from tests.evaluation.advisor_multiturn_task_eval import _policy_action
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_v5.json"
RESULT = ROOT / "tests/evaluation/results/advisor_multiturn_v5_runtime.json"
_TOOL_FOR_ACTION = {
    "SEARCH_PLACES": "search_places",
    "SEARCH_FOOD": "search_places",
    "REFINE_CANDIDATES": "search_places",
    "VERIFY_PLACES": "search_places",
    "COMPARE_LODGING": "search_places",
    "FILTER_INDOOR_CANDIDATES": "search_places",
    "SHOW_DEDUPLICATED_CANDIDATES": "search_places",
    "GET_TRIP_WEATHER": "get_trip_weather",
    "RETRY_READ_ONLY_TOOL": "get_trip_weather",
    "GET_DISTANCE_MATRIX": "get_distance_matrix",
    "BUILD_SCHEDULE_DRAFT": "build_schedule_draft",
    "REMOVE_CONFLICT_FROM_DRAFT": "build_schedule_draft",
    "UPDATE_DAY_SPECIFIC_CONSTRAINTS": "build_schedule_draft",
    "GET_TRIP_ROUTE": "get_trip_route",
}
_QUESTION_ACTIONS = {
    "ASK_USER", "ASK_USER_OR_LABEL_ASSUMPTION", "ASK_USER_OR_LABEL_UNVERIFIED",
    "ASK_USER_TO_RELAX_CONSTRAINT", "ASK_USER_FOR_SECOND_ENDPOINT",
}
_TASK_TYPES = {
    "DISCOVERY": "DISCOVERY", "SEARCH": "SEARCH", "MEAL": "MEAL",
    "LODGING": "LODGING", "SCHEDULE": "SCHEDULE", "TRANSPORT": "TRANSPORT",
    "WEATHER": "WEATHER", "PREPARATION": "PREPARATION", "MEMORY": "MEMORY",
    "CONTENT_IMPORT": "GENERAL",
}


class _FixtureInput(BaseModel):
    """Fixture handlers reconstruct their own inputs; model arguments are empty."""


def _promote_user_facts(state: dict[str, Any], text: str) -> None:
    match = re.search(
        r"(\d{1,2})月\s*(\d{1,2})日(?:到|至|—|-)?\s*(?:(\d{1,2})月)?\s*(\d{1,2})日?",
        text,
    )
    if match and state.get("trip_dates") is None:
        month_a, day_a, month_b, day_b = match.groups()
        state["trip_dates"] = [
            f"2026-{int(month_a):02d}-{int(day_a):02d}",
            f"2026-{int(month_b or month_a):02d}-{int(day_b):02d}",
        ]
    if state.get("destination") is None and "去成都" in text:
        state["destination"] = "成都"


def _runtime_status(action: str, loop_status: str) -> str:
    if action in _QUESTION_ACTIONS or action == "ASK_USER":
        return "WAITING_USER"
    if loop_status == "FAILED":
        return "PARTIAL"
    if action == "BUILD_SCHEDULE_DRAFT":
        return "PROPOSED"
    if loop_status in {"SUCCEEDED", "NEEDS_USER_INPUT"}:
        return "COMPLETED"
    return "PARTIAL"


async def _run_agent_turn(
    *, task_type: str, user: str, state: dict[str, Any], allowed_tools: list[str],
    prior_action: str | None, prior_status: str | None,
) -> dict[str, Any]:
    """Execute one scripted decision via the actual bounded production loop."""
    selected = _policy_action(
        task_type=task_type,
        user=user,
        state=state,
        allowed_tools=allowed_tools,
        prior_action=prior_action,
        prior_status=prior_status,
    )
    tool = _TOOL_FOR_ACTION.get(selected)
    question = selected.replace("_", " ").lower() if selected in _QUESTION_ACTIONS else None
    action = (
        AgentAction(kind="CALL_TOOL", tool=tool, arguments={}, summary=selected)
        if tool
        else AgentAction(kind="ASK_USER", question=question, summary=selected)
        if question
        else AgentAction(kind="FINISH", summary=selected)
    )
    fixture_calls: list[str] = []
    checkpoints: list[dict[str, Any]] = []

    async def handler(_arguments: BaseModel) -> dict[str, Any]:
        fixture_calls.append(tool or "")
        # Exercise the Runtime's failure path with a scoped, local fixture.
        provider_state = {
            "search_places": state.get("search_provider"),
            "get_trip_weather": state.get("weather_provider"),
            "get_distance_matrix": state.get("route_provider"),
            "get_trip_route": state.get("route_provider"),
        }.get(tool)
        if provider_state == "timeout":
            raise ToolFailure("FIXTURE_PROVIDER_TIMEOUT")
        return {"ok": True, "source": "LOCAL_FIXTURE"}

    async def trace(_event: dict[str, Any]) -> None:
        return None

    runtime = AgentRuntime(
        [
            ToolSpec(name, _FixtureInput, handler, retry_limit=0)
            for name in allowed_tools
        ],
        trace,
        max_steps=1,
        max_attempts=1,
        token_budget=0,
    )
    calls_left = [True]

    async def policy(_observation: dict[str, Any], _allowed: list[str]) -> AgentAction:
        if calls_left[0]:
            calls_left[0] = False
            return action
        return AgentAction(kind="FINISH", summary="local fixture action completed")

    async def verifier(_name: str, output: dict[str, Any]) -> bool:
        return output.get("ok") is True

    async def checkpoint(event: dict[str, Any]) -> None:
        checkpoints.append(event)

    contract = AgentTaskContext(
        task_type=_TASK_TYPES.get(task_type, "GENERAL"),
        goal=user[:1200] or "Continue the current travel task",
        known_facts=["fixture_state"],
        missing_facts=[],
        success_criteria=["perform one authorized action or ask the user"],
        allowed_tools=allowed_tools,
        approval_boundary=(
            "DRAFT_ONLY" if "build_schedule_draft" in allowed_tools else "READ_ONLY"
        ),
    )
    result = await run_constrained_loop(
        runtime=runtime,
        policy=policy,
        verifier=verifier,
        checkpoint=checkpoint,
        task_context=contract,
        initial_observation={"user_goal": user[:1200]},
        max_decisions=2,
        allowed_tool_names=set(allowed_tools),
    )
    first_tool = fixture_calls[0] if fixture_calls else None
    return {
        "action": selected,
        "tool": first_tool,
        "status": _runtime_status(selected, result.status),
        "loop_status": result.status,
        "transitions": result.transitions,
        "checkpoints": checkpoints,
        "allowlist_rejected": bool(tool and tool not in allowed_tools),
        "unauthorized_calls": [name for name in fixture_calls if name not in allowed_tools],
    }


async def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    contract = validate_dataset(dataset)
    predictions: list[dict[str, Any]] = []
    loop_status_counts: Counter[str] = Counter()
    selected_action_counts: Counter[str] = Counter()
    tool_call_count = 0
    user_question_count = 0
    allowlist_rejections = 0
    failures: list[str] = []

    for case in dataset["cases"]:
        state = dict(case["initial_state"])
        prior_action = None
        prior_status = None
        for index, turn in enumerate(case["turns"], start=1):
            if turn["action_owner"] != "AGENT_DECISION":
                # Product workflow, approval and runtime guards are intentionally
                # owned by their production endpoints, not exposed as model tools.
                prior_action = turn["expected_action"]
                prior_status = turn["expected_status"]
                _promote_user_facts(state, turn["user"])
                continue
            result = await _run_agent_turn(
                task_type=case["task_type"],
                user=turn["user"],
                state=state,
                allowed_tools=turn["allowed_tools"],
                prior_action=prior_action,
                prior_status=prior_status,
            )
            selected_action_counts[result["action"]] += 1
            loop_status_counts[result["loop_status"]] += 1
            tool_call_count += len(result["transitions"])
            user_question_count += result["status"] == "WAITING_USER"
            allowlist_rejections += result["allowlist_rejected"]
            if result["unauthorized_calls"]:
                failures.append(f"{case['id']}:{index}:unauthorized_tool")
            if result["tool"] and not result["transitions"]:
                failures.append(f"{case['id']}:{index}:missing_runtime_transition")
            predictions.append({
                "case_id": case["id"],
                "turn": index,
                "family": case["family"],
                "action_owner": turn["action_owner"],
                "actual_action": result["action"],
                "actual_status": result["status"],
                "tool": result["tool"],
                "allowlist_rejected": result["allowlist_rejected"],
                "loop_status": result["loop_status"],
                "transitions": result["transitions"],
                "checkpoints": result["checkpoints"],
            })
            prior_action, prior_status = result["action"], result["status"]
            _promote_user_facts(state, turn["user"])

    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    scored = len(predictions)
    report = {
        "evaluation_id": f"{contract['evaluation_id']}_runtime",
        "status": "PRODUCTION_CONSTRAINED_LOOP_LOCAL_FIXTURE_REPLAY",
        "dataset_id": contract["evaluation_id"],
        "provider_mode": "LOCAL_SYNTHETIC_TOOLS_AND_SCRIPTED_POLICY",
        "case_count": contract["case_count"],
        "turn_count": contract["turn_count"],
        "agent_decision_turns_executed": scored,
        "tool_calls_executed": tool_call_count,
        "user_questions_returned": user_question_count,
        "selected_action_counts": dict(sorted(selected_action_counts.items())),
        "loop_status_counts": dict(sorted(loop_status_counts.items())),
        "allowlist_rejections": allowlist_rejections,
        "unapproved_tool_calls": 0 if not failures else None,
        "failures": failures,
        "case_file_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source_provenance": evaluation_provenance("advisor_multiturn_runtime_local"),
        "turns": predictions,
        "interpretation": (
            "The frozen Agent-owned turns were exercised through the production constrained loop "
            "and AgentRuntime with local fixture handlers. This records runtime mechanics only, "
            "not action quality: the deterministic policy is scripted, in-sample and does not "
            "execute the full HTTP/DB Advisor service. Product-owned, approval and runtime-guard "
            "turns are excluded from model action execution. No LLM or external provider is called."
        ),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    import asyncio

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = asyncio.run(evaluate_dataset(dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"turns", "source_provenance"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
