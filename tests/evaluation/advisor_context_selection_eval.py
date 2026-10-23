"""Compare bounded context policies on the frozen synthetic Advisor set.

Default execution is a local preflight. Real model requests require --run-live
and explicit ceilings. This evaluates action selection only; tools are not
called and no TravelPilot trip/plan is written.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import unicodedata
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from travelpilot_api.settings import settings
from travelpilot_core.agents.planner import PlannerDecisionFailure, decide_next_action
from travelpilot_core.ports.tools import ToolFailure

from tests.evaluation import advisor_multiturn_live_eval as base_eval
from tests.evaluation.advisor_multiturn_contracts import validate_dataset

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_context_quality_v1.json"
RESULT = ROOT / "tests/evaluation/results/advisor_context_quality_v1_live.json"
PREFLIGHT_RESULT = ROOT / "tests/evaluation/results/advisor_context_quality_v1_preflight.json"
ARMS = ("RECENT_HISTORY", "STRUCTURED_CONTEXT", "SELECTIVE_CONTEXT")
MAX_OUTPUT_PER_REQUEST = 256
FRESH_STATUSES = {"CURRENT", "FRESH"}


def _context_items(case: dict[str, Any], arm: str) -> list[dict[str, str]]:
    items = case.get("initial_state", {}).get("context_items", [])
    if arm == "RECENT_HISTORY" or not isinstance(items, list):
        return []
    result = []
    for item in items[:8]:
        if not isinstance(item, dict) or item.get("authorized") is not True:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        freshness = str(item.get("freshness", "UNKNOWN"))[:24]
        if arm == "SELECTIVE_CONTEXT" and (
            item.get("applicable") is not True or freshness not in FRESH_STATUSES
        ):
            continue
        entry = {"type": str(item.get("kind", "context"))[:32], "content": content.strip()[:500]}
        if arm == "STRUCTURED_CONTEXT":
            entry["freshness"] = freshness
            entry["source_scope"] = "SYNTHETIC_TRIP_CONTEXT"
        result.append(entry)
    return result


def _bounded_visible_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Mirror production dialogue context: visible roles, last six, 400 chars each."""
    visible = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            visible.append({"role": role, "content": content[-400:]})
    return visible[-6:]


def build_observation(
    *, case: dict[str, Any], turn: dict[str, Any], turn_index: int,
    history: list[dict[str, Any]], arm: str, available_tools: list[str] | None = None,
) -> dict[str, Any]:
    state = dict(case.get("initial_state", {}))
    state.pop("context_items", None)
    observation: dict[str, Any] = {
        "task": case["task_type"],
        "user_goal": case["turns"][0]["user"],
        "current_user_message": turn["user"],
        "turn_number": turn_index + 1,
        "trip_context": state,
        "conversation_history": _bounded_visible_history(history),
        "tool_observations": [
            {"tool": item["tool"], "result": item["result"]}
            for item in history if item.get("role") == "tool_observation"
            and isinstance(item.get("tool"), str) and isinstance(item.get("result"), dict)
        ][-4:],
        "available_tools": sorted(available_tools if available_tools is not None else turn["allowed_tools"]),
        "tool_observations_are_local_fixtures": True,
        "policy": (
            "Use only available read-only tools. Preserve explicit corrections and confirmed choices. "
            "Never write a formal plan, infer approval, invent evidence, or claim synthetic facts are live."
        ),
    }
    items = _context_items(case, arm)
    if items:
        observation["supplemental_context"] = items
    if arm == "STRUCTURED_CONTEXT":
        observation["context_sources"] = "authorized items with freshness labels"
    elif arm == "SELECTIVE_CONTEXT":
        observation["context_sources"] = "authorized applicable current/fresh items only"
    return observation


def validate_inputs(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    report = validate_dataset(dataset)
    cases = dataset.get("cases")
    if dataset.get("dataset_id") != "advisor_context_quality_v1" or not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("expected the frozen 20-case context-quality dataset")
    prompts = {
        " ".join(unicodedata.normalize("NFKC", turn["user"]).split()).casefold()
        for case in cases for turn in case["turns"]
    }
    overlap_count = 0
    max_prompt_chars = 0
    observed_hashes: dict[str, str] = {}
    for other in path.parent.glob("*.json"):
        if other.resolve() == path.resolve():
            continue
        try:
            data = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        dataset_id = data.get("dataset_id", other.stem)
        observed_hashes[dataset_id] = hashlib.sha256(other.read_bytes()).hexdigest()
        old_prompts = {
            " ".join(unicodedata.normalize("NFKC", turn["user"]).split()).casefold()
            for case in data.get("cases", []) if isinstance(case, dict)
            for turn in case.get("turns", [])
            if isinstance(turn, dict) and isinstance(turn.get("user"), str)
        }
        overlap_count += len(prompts & old_prompts)
    if overlap_count:
        raise ValueError(f"found {overlap_count} exact normalized prompt overlaps")
    frozen_hashes = dataset.get("reference_dataset_sha256")
    if not isinstance(frozen_hashes, dict) or frozen_hashes != observed_hashes:
        raise ValueError("historical case corpus changed since this set was frozen; refreeze and review separation")

    for case in cases:
        if len(case["turns"]) != 2:
            raise ValueError(f"{case['id']}: expected two decisions")
        history: list[dict[str, Any]] = []
        for index, turn in enumerate(case["turns"]):
            schemas = base_eval._allowed_tool_schemas(case, turn, history)
            if not set(schemas).issubset(set(turn["allowed_tools"])):
                raise ValueError(f"{case['id']}: dynamic schema exceeded the frozen tool allowlist")
            observations = [
                build_observation(case=case, turn=turn, turn_index=index, history=history,
                                  arm=arm, available_tools=list(schemas))
                for arm in ARMS
            ]
            base = observations[0]
            for actual in observations[1:]:
                for key in ("task", "user_goal", "current_user_message", "turn_number", "trip_context", "available_tools", "policy", "conversation_history", "tool_observations"):
                    if actual.get(key) != base.get(key):
                        raise ValueError(f"{case['id']}: context arm changed shared field {key}")
            for actual in observations[1:]:
                for item in actual.get("supplemental_context", []):
                    if set(item) & {"applicable", "authorized", "expected_action", "adjudication_note"}:
                        raise ValueError(f"{case['id']}: evaluator-only label leaked into model input")
            for arm, arm_observation in zip(ARMS, observations, strict=True):
                prompt = {
                    "observation": arm_observation,
                    "allowed_tools": {name: schema.get("input_schema", {}) for name, schema in schemas.items()},
                    "allowed_actions": ["CALL_TOOL", "ASK_USER", "FINISH"],
                }
                prompt_chars = len(json.dumps(prompt, ensure_ascii=False, separators=(",", ":")))
                max_prompt_chars = max(max_prompt_chars, prompt_chars)
                if prompt_chars > 4000:
                    raise ValueError(
                        f"{case['id']} turn {index + 1} {arm}: planner input would be truncated ({prompt_chars}/4000 chars)"
                    )
            if index == 0:
                expected = turn["expected_action"]
                tool = base_eval.EXPECTED_TOOL.get(expected)
                if tool:
                    history.extend([
                        {"role": "user", "content": turn["user"]},
                        {"role": "assistant", "content": "我已记录这项安排，正在继续处理。"},
                        {"role": "tool_observation", "tool": tool,
                         "result": _fixture_observation(tool, case, {})},
                    ])

    report.update({
        "case_count": len(cases),
        "decision_count": len(cases) * 2,
        "policy_arms": list(ARMS),
        "planned_requests": len(cases) * 2 * len(ARMS),
        "planned_output_token_ceiling_at_256": len(cases) * 2 * len(ARMS) * MAX_OUTPUT_PER_REQUEST,
        "exact_normalized_prompt_overlap_count": overlap_count,
        "arm_equivalence_check": "passed: task, goal, turn, trip state, tools and safety contract are identical",
        "max_serialized_planner_input_chars": max_prompt_chars,
        "planner_input_truncation_boundary_chars": 4000,
        "live_model_calls": 0,
    })
    return dataset, report


def _fingerprint(value: dict[str, Any]) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _fixture_observation(tool: str, case: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    state = case.get("initial_state", {})
    if tool == "get_trip_weather" and state.get("weather_provider") == "timeout":
        return {
            "status": "FAILED_FIXTURE", "error_code": "WEATHER_TIMEOUT",
            "message": "Synthetic provider timeout; no forecast evidence was returned.",
            "source": "LOCAL_FIXTURE",
        }
    return base_eval._fixture_observation(tool, case, arguments)


async def run_live(path: Path, case_ids: tuple[str, ...], token_cap: int, max_requests: int, max_tokens: int) -> dict[str, Any]:
    dataset, preflight = validate_inputs(path)
    if not settings.llm_api_key or not settings.llm_model or not settings.llm_base_url:
        raise RuntimeError("LLM provider settings are incomplete; no request was sent")
    if not 1 <= token_cap <= MAX_OUTPUT_PER_REQUEST:
        raise ValueError(f"per-request cap must be 1..{MAX_OUTPUT_PER_REQUEST}")
    by_id = {case["id"]: case for case in dataset["cases"]}
    if not case_ids or len(set(case_ids)) != len(case_ids) or any(key not in by_id for key in case_ids):
        raise ValueError("case IDs must be distinct valid frozen cases")
    cases = [by_id[key] for key in case_ids]
    request_ceiling = len(cases) * 2 * len(ARMS)
    token_ceiling = request_ceiling * token_cap
    if request_ceiling > 120 or max_requests < request_ceiling or max_tokens < token_ceiling:
        raise ValueError(f"selection requires authorization for {request_ceiling} requests and {token_ceiling} output tokens")

    checkpoint_path = RESULT.with_suffix(".checkpoint.jsonl")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps({
        "evaluation_id": dataset["dataset_id"],
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "checkpoint_type": "per-decision-redacted-action-and-usage",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    groups: dict[str, list[dict[str, Any]]] = {}
    requests = input_total = output_total = 0
    for arm in ARMS:
        arm_rows = []
        for case in cases:
            history: list[dict[str, Any]] = []
            turns = []
            for i, turn in enumerate(case["turns"]):
                schemas = base_eval._allowed_tool_schemas(case, turn, history)
                if not set(schemas).issubset(set(turn["allowed_tools"])):
                    raise ValueError(f"{case['id']}: schema/allowlist mismatch")
                observation = build_observation(
                    case=case, turn=turn, turn_index=i, history=history, arm=arm,
                    available_tools=list(schemas),
                )
                started = time.perf_counter()
                requests += 1
                try:
                    decision, usage = await decide_next_action(
                        observation, schemas, api_key=settings.llm_api_key,
                        model=settings.llm_model, base_url=settings.llm_base_url,
                        token_budget=token_cap,
                    )
                    action = decision.model_dump(mode="json")
                    valid_args, errors = base_eval._validate_tool_arguments(
                        action.get("tool"), action.get("arguments"), case.get("initial_state"),
                    ) if action.get("kind") == "CALL_TOOL" else (True, [])
                    allowed = action.get("kind") != "CALL_TOOL" or action.get("tool") in schemas
                    valid = allowed and valid_args
                    matched = base_eval._expected_match(turn, action) if valid else False
                    usage_data = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else asdict(usage)
                    used_input = int(usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0)) or 0)
                    used_output = int(usage_data.get("output_tokens", usage_data.get("completion_tokens", 0)) or 0)
                    error = None
                except PlannerDecisionFailure as exc:
                    action, valid, allowed, matched = None, False, False, False
                    usage_data = asdict(exc.usage)
                    used_input = int(usage_data.get("prompt_tokens", 0) or 0)
                    used_output = int(usage_data.get("completion_tokens", 0) or 0)
                    errors, error = [{"type": exc.code}], exc.code
                except ToolFailure as exc:
                    action, valid, allowed, matched = None, False, False, False
                    errors, used_input, used_output, error = [{"type": exc.code}], 0, 0, exc.code
                latency = round((time.perf_counter() - started) * 1000, 2)
                input_total += used_input
                output_total += used_output
                if used_output > token_cap or output_total > max_tokens or requests > max_requests:
                    raise RuntimeError("provider usage exceeded the explicit evaluation ceiling")
                turn_result = {
                    "turn": i + 1, "expected_action": turn["expected_action"],
                    "action": action, "action_valid": valid, "allowlist_valid": allowed,
                    "argument_errors": errors, "expected_action_match": matched,
                    "input_tokens": used_input, "output_tokens": used_output,
                    "latency_ms": latency, "context_sha256": _fingerprint(observation),
                    "provider_error": error,
                }
                turns.append(turn_result)
                with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
                    checkpoint.write(json.dumps({
                        "arm": arm, "case_id": case["id"], **turn_result,
                        "request_number": requests, "output_tokens_so_far": output_total,
                    }, ensure_ascii=False) + "\n")
                    checkpoint.flush()
                history.append({"role": "user", "content": turn["user"]})
                if action is not None:
                    history.append({"role": "assistant", "content": "我已记录这项安排，正在继续处理。"})
                    tool_name = action.get("tool") if action.get("kind") == "CALL_TOOL" and valid else None
                    if tool_name:
                        fixture = _fixture_observation(tool_name, case, action.get("arguments") or {})
                        history.append({"role": "tool_observation", "tool": tool_name, "result": fixture})
            arm_rows.append({
                "case_id": case["id"], "turns": turns,
                "sequence_match": all(row["expected_action_match"] is True for row in turns),
            })
        groups[arm] = arm_rows

    summary = {}
    for arm, rows in groups.items():
        turns = [turn for row in rows for turn in row["turns"]]
        matches = sum(turn["expected_action_match"] is True for turn in turns)
        summary[arm] = {
            "case_count": len(rows), "decision_count": len(turns),
            "action_matches": matches, "action_match_rate": round(matches / max(1, len(turns)), 4),
            "complete_sequences": sum(row["sequence_match"] for row in rows),
            "invalid_actions": sum(not turn["action_valid"] for turn in turns),
            "allowlist_violations": sum(not turn["allowlist_valid"] for turn in turns),
            "input_tokens": sum(turn["input_tokens"] for turn in turns),
            "output_tokens": sum(turn["output_tokens"] for turn in turns),
            "mean_latency_ms": round(sum(turn["latency_ms"] for turn in turns) / max(1, len(turns)), 2),
            "runs": rows,
        }
    return {
        "evaluation_id": dataset["dataset_id"],
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "model": settings.llm_model,
        "provider_host": settings.llm_base_url.split("/")[2] if "://" in settings.llm_base_url else "configured",
        "requests_used": requests, "output_token_cap_per_request": token_cap,
        "output_tokens_reported": output_total, "input_tokens_reported": input_total,
        "authorization_ceiling": {"requests": max_requests, "output_tokens": max_tokens},
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "preflight": preflight, "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--token-cap", type=int, default=MAX_OUTPUT_PER_REQUEST)
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=0)
    args = parser.parse_args()
    dataset, report = validate_inputs(args.dataset)
    if not args.run_live:
        output = args.output or PREFLIGHT_RESULT
        report["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in (
            "evaluation_id", "case_count", "decision_count", "planned_requests",
            "exact_normalized_prompt_overlap_count", "live_model_calls", "dataset_sha256",
        )}, ensure_ascii=False, indent=2))
        return
    output = args.output or RESULT
    ids = tuple(args.case_id or [case["id"] for case in dataset["cases"]])
    result = asyncio.run(run_live(args.dataset, ids, args.token_cap, args.max_requests, args.max_output_tokens))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("evaluation_id", "model", "requests_used", "output_tokens_reported", "summary")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
