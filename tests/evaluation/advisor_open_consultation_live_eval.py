"""Run one bounded live action decision for each frozen open-consultation case.

This is an action-selection smoke, not a full dialogue or user-quality benchmark.
It makes no database writes and stores no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from travelpilot_api.settings import settings
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.ports.tools import ToolFailure

from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
CASE_FILE = ROOT / "tests/evaluation/cases/advisor_open_consultation_v2.json"
OUTPUT_FILE = ROOT / "tests/evaluation/results/advisor_open_consultation_live_v2_20261018.json"
TOKEN_CAP = 128

TOOL_SCHEMAS = {
    "get_trip_context": {"input_schema": {
        "type": "object", "properties": {"trip_id": {"type": "string"}},
        "required": ["trip_id"],
    }},
    "search_places": {"input_schema": {
        "type": "object", "properties": {
            "keyword": {"type": "string"}, "city": {"type": "string"},
        }, "required": ["keyword"],
    }},
    "get_trip_weather": {"input_schema": {"type": "object", "properties": {}}},
    "get_distance_matrix": {"input_schema": {
        "type": "object", "properties": {"points": {"type": "array"}},
        "required": ["points"],
    }},
    "build_schedule_draft": {"input_schema": {"type": "object", "properties": {}}},
    "prepare_confirmation_preview": {"input_schema": {"type": "object", "properties": {}}},
}


def _matches(case: dict[str, Any], action: dict[str, Any]) -> bool:
    expected = case["expected_next_action"]
    kind, tool = action.get("kind"), action.get("tool")
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    if expected == "ASK_USER":
        return kind == "ASK_USER" and bool(action.get("question") or action.get("summary"))
    if expected == "SEARCH_PLACES":
        return kind == "CALL_TOOL" and tool == "search_places"
    if expected == "SEARCH_FOOD":
        return kind == "CALL_TOOL" and tool == "search_places" and any(
            word in str(args.get("keyword", "")) for word in ("餐饮", "美食", "午餐")
        )
    if expected == "SEARCH_LODGING":
        return kind == "CALL_TOOL" and tool == "search_places" and any(
            word in str(args.get("keyword", "")) for word in ("住宿", "酒店", "民宿")
        )
    if expected == "GET_WEATHER":
        return kind == "CALL_TOOL" and tool == "get_trip_weather"
    if expected == "READ_TRIP_AND_ROUTE":
        return kind == "CALL_TOOL" and tool in {"get_trip_context", "get_distance_matrix"}
    if expected == "SAFE_STOP_OR_QUALIFIED_ESTIMATE":
        return kind in {"ASK_USER", "FINISH"} or (
            kind == "CALL_TOOL" and tool == "build_schedule_draft"
        )
    if expected == "ASK_CONFIRMATION_OR_COMMIT_CONFIRMED_DRAFT":
        return kind == "ASK_USER" or (
            kind == "CALL_TOOL" and tool == "prepare_confirmation_preview"
        )
    return False


async def _score(case: dict[str, Any], variant: str) -> dict[str, Any]:
    available_tools = {
        name: schema for name, schema in TOOL_SCHEMAS.items()
        if name in case.get("available_tools", [])
    }
    observation: dict[str, Any] = {
        "task": "select_the_next_safe_advisor_action",
        "user_goal": case["user_goal"],
        "trip_mode": case["mode"],
        "known_facts": case.get("known_facts", {}),
        "available_read_only_tools": sorted(available_tools),
    }
    if variant == "B3_VERIFIER_RECOVERY":
        observation["verification_contract"] = [
            "Reject stale or malformed evidence.",
            "Do not use a tool outside the supplied allowlist.",
            "Do not perform a formal write; confirmation is a separate user action.",
            "If a required fact is missing or a provider fails, ask or stop safely.",
        ]
        observation["recovery_contract"] = "On failure, choose a bounded safe next action; never repeat blindly."

    try:
        decision, usage = await decide_next_action(
            observation,
            available_tools,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            token_budget=TOKEN_CAP,
        )
        action = decision.model_dump(mode="json")
        args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        valid_tool = action.get("kind") != "CALL_TOOL" or action.get("tool") in available_tools
        if valid_tool and action.get("kind") == "CALL_TOOL" and action.get("tool") == "search_places":
            destination = case.get("known_facts", {}).get("destination")
            city = args.get("city")
            valid_tool = isinstance(args.get("keyword"), str) and (
                city is None or destination is None or city == destination
            )
        if valid_tool and action.get("kind") == "CALL_TOOL" and action.get("tool") == "get_trip_weather":
            valid_tool = not args
        return {
            "case_id": case["id"],
            "action": action,
            "expected_action_match": _matches(case, action),
            "allowlist_valid": valid_tool,
            "request_status": "SUCCEEDED",
            "prompt_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
    except PlannerDecisionFailure as exc:
        return {
            "case_id": case["id"], "action": None,
            "expected_action_match": False, "allowlist_valid": False,
            "request_status": exc.code,
            "prompt_tokens": exc.usage.prompt_tokens,
            "output_tokens": exc.usage.completion_tokens,
            "total_tokens": exc.usage.total_tokens,
        }
    except ToolFailure as exc:
        return {
            "case_id": case["id"], "action": None,
            "expected_action_match": False, "allowlist_valid": False,
            "request_status": exc.code,
            "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        }


async def run_live(case_file: Path = CASE_FILE) -> dict[str, Any]:
    if not settings.llm_api_key or not settings.llm_model or not settings.llm_base_url:
        raise RuntimeError("LLM provider settings are incomplete; no request was sent")
    dataset = json.loads(case_file.read_text(encoding="utf-8"))
    rows: dict[str, list[dict[str, Any]]] = {}
    for variant in ("B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"):
        rows[variant] = []
        for case in dataset["cases"]:
            rows[variant].append(await _score(case, variant))
    digest = hashlib.sha256(case_file.read_bytes()).hexdigest()
    groups = {
        variant: {
            "request_count": len(items),
            "expected_action_matches": sum(row["expected_action_match"] for row in items),
            "allowlist_violations": sum(not row["allowlist_valid"] for row in items),
            "output_tokens": sum(row["output_tokens"] for row in items),
            "prompt_tokens": sum(row["prompt_tokens"] for row in items),
            "runs": items,
        }
        for variant, items in rows.items()
    }
    return {
        "evaluation_id": dataset["dataset_id"],
        "provider_mode": "LIVE_MODEL_SINGLE_DECISION_PER_FROZEN_CASE",
        "model": settings.llm_model,
        "base_url_host": settings.llm_base_url.split("/")[2] if "://" in settings.llm_base_url else None,
        "requests": sum(group["request_count"] for group in groups.values()),
        "per_request_output_token_cap": TOKEN_CAP,
        "requested_output_token_ceiling": 2 * len(dataset["cases"]) * TOKEN_CAP,
        "dataset_sha256": digest,
        "source_provenance": evaluation_provenance("advisor_open_consultation_live"),
        "run_at_utc": datetime.now(UTC).isoformat(),
        "scope": "One decision per prompt; measures first-action selection only, not dialogue quality, tool execution, travel data quality or end-to-end completion. Per-case state and tool allowlists are server supplied.",
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true", help="Send the bounded 20 model requests")
    parser.add_argument("--dataset", type=Path, default=CASE_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    if not args.run_live:
        raise SystemExit("Pass --run-live to explicitly execute the bounded evaluation")
    result = asyncio.run(run_live(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "requests": result["requests"],
        "groups": {
            name: {key: value for key, value in group.items() if key != "runs"}
            for name, group in result["groups"].items()
        },
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
