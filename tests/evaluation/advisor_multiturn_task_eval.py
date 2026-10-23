"""Offline scripted execution score for the frozen Advisor multi-turn suite.

This is a deterministic policy simulation. It does not call an LLM, provider,
or product HTTP endpoint and must not be presented as model-quality evidence.
The policy receives only user text, initial task facts, prior simulated outcomes,
and the currently authorized tools. Frozen expected actions are read only by the
scorer after each prediction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tests.evaluation.advisor_multiturn_contracts import validate_dataset
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_v5.json"
RESULT = ROOT / "tests/evaluation/results/advisor_multiturn_v5_scripted.json"
CONFIRM = re.compile(r"确认|同意保存|确认保存|保存吧|确定保存|就按这个", re.IGNORECASE)
TIME = re.compile(r"\b(?:[01]?\d|2[0-3])[:：点时][0-5]?\d?\b")


def _contains(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def _policy_action(
    *,
    task_type: str,
    user: str,
    state: dict[str, Any],
    allowed_tools: list[str],
    prior_action: str | None,
    prior_status: str | None,
) -> str:
    """Choose an action without access to expected_action/expected_status."""
    text = user.strip()
    tools = set(allowed_tools)
    if prior_status == "WAITING_USER" and _contains(text, "取消", "停止", "算了"):
        return "CANCEL_PENDING_TASK"

    if _contains(text, "订酒店", "直接预订", "帮我下单", "代我预订"):
        return "EXPLAIN_UNSUPPORTED_BOOKING"
    if state.get("foreign_place_reference") and _contains(text, "路线", "怎么去", "导航"):
        return "REJECT_UNOWNED_PLACE"
    if state.get("model_action") == "unknown_tool":
        return "REJECT_INVALID_ACTION"
    if state.get("context_revision") is not None and _contains(text, "11月", "12月", "补充日期"):
        return "REVALIDATE_TRIP_CONTEXT"
    if state.get("search_provider") == "timeout" and prior_status == "PARTIAL":
        return "REPORT_TOOL_FAILURE"
    if state.get("weather_provider") == "timeout" and prior_status == "PARTIAL":
        if _contains(text, "再查", "重试", "再试"):
            return "RETRY_READ_ONLY_TOOL"
        if prior_action == "RETRY_READ_ONLY_TOOL":
            return "STOP_AFTER_BOUNDED_RETRIES"
        return "ASK_USER_OR_LABEL_ASSUMPTION"
    if state.get("route_provider") == "timeout" and prior_status == "PARTIAL":
        return "ASK_CONFIRMATION_OR_LABEL_ESTIMATE"
    saved_places = state.get("saved_places")
    if (
        prior_action == "GET_TRIP_WEATHER"
        and _contains(text, "室内", "景点", "地点")
        and "search_places" in tools
    ):
        return "SEARCH_PLACES"
    if prior_action == "GET_TRIP_ROUTE" and state.get("mobility_need"):
        return "LABEL_MOBILITY_LIMIT"
    if prior_action == "GET_TRIP_ROUTE" and _contains(text, "公交", "地铁", "步行", "分段"):
        return "PRESENT_ROUTE_SEGMENTS"
    route_request = _contains(text, "路线", "怎么去", "公交", "地铁", "驾车", "步行", "通勤")
    if route_request and isinstance(saved_places, list):
        mentioned_places = [
            place for place in saved_places if isinstance(place, str) and place in text
        ]
        endpoints_are_clear = len(mentioned_places) >= 2 or (
            len(saved_places) == 2 and state.get("endpoints") != "ambiguous"
        )
        if endpoints_are_clear and "get_trip_route" in tools:
            return "GET_TRIP_ROUTE"
        if len(saved_places) < 2:
            return "ASK_USER"
        return "ASK_USER_FOR_SECOND_ENDPOINT"

    if task_type == "MEMORY":
        if state.get("sensitive_inference"):
            return "DECLINE_SENSITIVE_INFERENCE"
        if state.get("memory_enabled") is False:
            return (
                "USE_SESSION_ONLY" if _contains(text, "记住", "偏好") else "EXPLAIN_MEMORY_DISABLED"
            )
        if _contains(text, "删除", "移除"):
            if prior_status == "WAITING_USER" and CONFIRM.search(text):
                return "DELETE_CONFIRMED_MEMORY"
            return "LIST_OWNER_MEMORIES" if prior_action is None else "ASK_CONFIRMATION"
        if _contains(text, "替换", "更新", "改成", "更喜欢"):
            if prior_status == "WAITING_USER" and CONFIRM.search(text):
                return "UPDATE_CONFIRMED_MEMORY"
            return "ASK_MEMORY_UPDATE_CONSENT"
        if _contains(text, "记住", "以后", "我喜欢", "偏好"):
            if prior_status == "WAITING_USER" and CONFIRM.search(text):
                return "SAVE_CONFIRMED_MEMORY"
            if prior_action == "PROPOSE_MEMORY" and CONFIRM.search(text):
                return "SAVE_CONFIRMED_MEMORY"
            return "PROPOSE_MEMORY"
        return "ASK_MEMORY_CONSENT"

    if state.get("source_type") == "social_link" and state.get("fetch_available") is False:
        return "EXPLAIN_LINK_UNAVAILABLE"
    if state.get("source_type") == "image":
        if prior_action == "OCR_EXTRACT" and _contains(text, "选", "加入", "地点"):
            return "VERIFY_PLACES" if "search_places" in tools else "SHOW_UNVERIFIED_CANDIDATES"
        if prior_action == "VERIFY_PLACES" and task_type == "SCHEDULE":
            return "BUILD_SCHEDULE_DRAFT"
        if state.get("ocr_confidence") == "low" and prior_action == "OCR_EXTRACT":
            return "SHOW_UNCERTAIN_OCR"
        if state.get("ocr_confidence") == "mixed" and prior_action == "OCR_EXTRACT":
            return "SHOW_UNVERIFIED_CANDIDATES"
        return "OCR_EXTRACT"
    if state.get("source_type") == "text":
        if state.get("city") == "unknown" and prior_action == "EXTRACT_CANDIDATES":
            return "ASK_USER"
        if state.get("duplicates") and prior_action == "EXTRACT_CANDIDATES":
            return "SHOW_DEDUPLICATED_CANDIDATES"
        if (
            prior_action == "EXTRACT_CANDIDATES"
            and _contains(text, "确认", "加入", "地点")
            and "search_places" in tools
        ):
            return "VERIFY_PLACES"
        return "EXTRACT_CANDIDATES"

    if task_type in {"WEATHER", "PREPARATION"}:
        if not state.get("destination"):
            return "ASK_USER"
        if not state.get("dates") and not _contains(text, "11月", "12月", "月", "日期"):
            return "ASK_USER"
        if state.get("forecast_status") == "OUT_OF_RANGE":
            return "EXPLAIN_FORECAST_RANGE"
        if "get_trip_weather" in tools:
            return "GET_TRIP_WEATHER"
        if prior_action == "GET_TRIP_WEATHER" or _contains(text, "下雨", "晴天", "天气"):
            return (
                "GIVE_NON_FORECAST_ADVICE"
                if state.get("forecast_status") == "OUT_OF_RANGE"
                else "GIVE_GROUNDED_PREPARATION_ADVICE"
            )

    if task_type == "TRANSPORT" or _contains(
        text, "路线", "怎么去", "公交", "地铁", "驾车", "步行", "通勤"
    ):
        if (
            prior_action == "SEARCH_PLACES"
            and state.get("trip_places")
            and "get_trip_route" in tools
        ):
            return "GET_TRIP_ROUTE"
        if state.get("endpoints") == "ambiguous" or state.get("origin") is None:
            return (
                "ASK_USER"
                if not isinstance(saved_places, list) or len(saved_places) < 2
                else "ASK_USER_FOR_SECOND_ENDPOINT"
            )
        if state.get("mobility_need") and "get_trip_route" in tools:
            return "GET_TRIP_ROUTE"
        return "GET_TRIP_ROUTE" if "get_trip_route" in tools else "ASK_USER"

    if task_type == "LODGING" or _contains(text, "酒店", "住宿", "住哪", "住在"):
        if _contains(text, "订酒店", "直接预订"):
            return "EXPLAIN_UNSUPPORTED_BOOKING"
        if _contains(text, "路线", "交通", "怎么去") and prior_action == "SEARCH_PLACES":
            return "GET_TRIP_ROUTE" if "get_trip_route" in tools else "ASK_USER"
        if "search_places" in tools:
            return "SEARCH_PLACES"
        return "LABEL_ACCESSIBILITY_UNVERIFIED"

    if task_type == "MEAL" or _contains(text, "午餐", "晚餐", "早餐", "吃饭", "餐厅", "美食"):
        if _contains(text, "景点", "行程") and "search_places" in tools:
            return "SEARCH_FOOD"
        if prior_status == "WAITING_USER" and _contains(text, "推荐", "帮我找", "可以"):
            return "SEARCH_PLACES" if "search_places" in tools else "ASK_USER"
        if _contains(text, "选第", "选择第", "点", "时间") and TIME.search(text):
            return "SET_MEAL_TIME"
        if _contains(text, "目前", "缺", "空档"):
            return "ASK_USER"
        return "SEARCH_PLACES" if "search_places" in tools else "ASK_USER"

    if task_type == "SCHEDULE" or _contains(text, "行程", "安排", "排程", "规划", "优化"):
        if _contains(text, "午餐", "晚餐", "早餐", "餐饮", "吃饭") and "search_places" in tools:
            return "SEARCH_FOOD"
        if (
            prior_status == "WAITING_USER"
            and _contains(text, "草案", "方案")
            and not CONFIRM.search(text)
        ):
            return "ASK_CONFIRMATION_FOR_BOTH_CHANGES"
        if prior_status == "PROPOSED" and _contains(text, "合适", "看看", "看方案", "可以"):
            return "ASK_CONFIRMATION"
        if prior_status == "PROPOSED" and prior_action == "GET_DISTANCE_MATRIX":
            return "BUILD_SCHEDULE_DRAFT"
        if prior_action == "SEARCH_FOOD" and "build_schedule_draft" in tools:
            return "BUILD_SCHEDULE_DRAFT"
        if prior_action == "BUILD_SCHEDULE_DRAFT" and _contains(
            text, "今天", "明天", "每天不同", "节奏"
        ):
            return "UPDATE_DAY_SPECIFIC_CONSTRAINTS"
        if prior_status == "WAITING_USER" and CONFIRM.search(text):
            return "COMMIT_CONFIRMED_DRAFT"
        if _contains(text, "今天", "明天", "紧凑", "轻松") and "build_schedule_draft" in tools:
            return "BUILD_SCHEDULE_DRAFT"
        if state.get("trip_dates") is None and not _contains(text, "月", "日期"):
            return "ASK_USER"
        if int(state.get("saved_places", 0) or 0) > 6 and _contains(text, "全部", "都"):
            return "ASK_USER_TO_RELAX_CONSTRAINT"
        if "get_distance_matrix" in tools:
            return "GET_DISTANCE_MATRIX"
        if "build_schedule_draft" in tools:
            return "BUILD_SCHEDULE_DRAFT"
        return "ASK_USER"

    if prior_status == "WAITING_USER" and CONFIRM.search(text):
        return "ASK_CONFIRMATION"
    if "search_places" in tools:
        return "SEARCH_PLACES"
    if "get_trip_weather" in tools and _contains(text, "天气", "下雨", "带伞"):
        return "GET_TRIP_WEATHER"
    return "ASK_USER"


def _status_for(action: str, state: dict[str, Any]) -> str:
    if action in {
        "ASK_USER",
        "ASK_USER_FOR_SECOND_ENDPOINT",
        "ASK_USER_TO_RELAX_CONSTRAINT",
        "ASK_USER_OR_LABEL_ASSUMPTION",
        "ASK_USER_OR_LABEL_UNVERIFIED",
        "ASK_CONFIRMATION",
        "ASK_CONFIRMATION_FOR_BOTH_CHANGES",
        "ASK_MEMORY_CONSENT",
        "ASK_MEMORY_UPDATE_CONSENT",
        "ASK_CONFIRMATION_OR_LABEL_ESTIMATE",
        "SHOW_UNVERIFIED_CANDIDATES",
        "SHOW_UNCERTAIN_OCR",
    }:
        return "WAITING_USER"
    if action == "CANCEL_PENDING_TASK":
        return "CANCELLED"
    if action == "REVALIDATE_TRIP_CONTEXT":
        return "STALE"
    if action in {
        "GET_DISTANCE_MATRIX",
        "BUILD_SCHEDULE_DRAFT",
        "ASK_CONFIRMATION_FOR_BOTH_CHANGES",
    }:
        return "PROPOSED"
    if action in {"SEARCH_PLACES", "GET_TRIP_WEATHER", "GET_TRIP_ROUTE", "VERIFY_PLACES"}:
        if any(
            state.get(key) == "timeout"
            for key in (
                "search_provider",
                "weather_provider",
                "route_provider",
            )
        ):
            return "PARTIAL"
        return "COMPLETED"
    if action in {"OCR_EXTRACT", "EXTRACT_CANDIDATES"}:
        return "PARTIAL"
    if action in {"REJECT_INVALID_ACTION", "SAFE_STOP"}:
        return "PARTIAL"
    return "COMPLETED"


def _fixed_action(task_type: str, allowed_tools: list[str]) -> str:
    """B1 approximation: one task-type workflow, with no state adaptation."""
    tools = set(allowed_tools)
    tool = {
        "DISCOVERY": ("search_places", "SEARCH_PLACES"),
        "SEARCH": ("search_places", "SEARCH_PLACES"),
        "MEAL": ("search_places", "SEARCH_PLACES"),
        "LODGING": ("search_places", "SEARCH_PLACES"),
        "SCHEDULE": (
            "get_distance_matrix" if "get_distance_matrix" in tools else "build_schedule_draft",
            "GET_DISTANCE_MATRIX" if "get_distance_matrix" in tools else "BUILD_SCHEDULE_DRAFT",
        ),
        "TRANSPORT": ("get_trip_route", "GET_TRIP_ROUTE"),
        "WEATHER": ("get_trip_weather", "GET_TRIP_WEATHER"),
        "PREPARATION": ("get_trip_weather", "GET_TRIP_WEATHER"),
    }.get(task_type)
    if tool and tool[0] in tools:
        return tool[1]
    return "ASK_USER"


def _run_policy(
    case: dict[str, Any],
    *,
    variant: str,
) -> dict[str, Any]:
    guarded = variant == "B3_GUARDED_POLICY"
    state = dict(case["initial_state"])
    previous_action: str | None = None
    previous_status: str | None = None
    predictions: list[dict[str, Any]] = []
    tool_calls: list[str] = []
    violations: list[str] = []
    for index, turn in enumerate(case["turns"], start=1):
        action = (
            _fixed_action(case["task_type"], turn["allowed_tools"])
            if variant == "B1_FIXED_POLICY"
            else _policy_action(
                task_type=case["task_type"],
                user=turn["user"],
                state=state,
                allowed_tools=turn["allowed_tools"],
                prior_action=previous_action,
                prior_status=previous_status,
            )
        )
        tool_for_action = {
            "SEARCH_PLACES": "search_places",
            "SEARCH_FOOD": "search_places",
            "GET_TRIP_WEATHER": "get_trip_weather",
            "GET_DISTANCE_MATRIX": "get_distance_matrix",
            "BUILD_SCHEDULE_DRAFT": "build_schedule_draft",
            "GET_TRIP_ROUTE": "get_trip_route",
            "VERIFY_PLACES": "search_places",
        }.get(action)
        if tool_for_action:
            if tool_for_action not in turn["allowed_tools"]:
                if guarded:
                    action = "SAFE_STOP"
                    tool_for_action = None
                else:
                    violations.append(f"{case['id']}:{index}:tool_not_allowed:{tool_for_action}")
            else:
                tool_calls.append(tool_for_action)
        if (
            action
            in {
                "COMMIT_CONFIRMED_DRAFT",
                "COMMIT_CONFIRMED_CANDIDATES",
                "SAVE_CONFIRMED_MEMORY",
                "UPDATE_CONFIRMED_MEMORY",
                "DELETE_CONFIRMED_MEMORY",
            }
            and guarded
            and (turn.get("confirmation_received") is not True or previous_status != "WAITING_USER")
        ):
            action = "SAFE_STOP"
            violations.append(f"{case['id']}:{index}:approval_gate")
        status = _status_for(action, state)
        predictions.append(
            {
                "action": action,
                "status": status,
                "expected_action_match": action == turn["expected_action"],
                "expected_status_match": status == turn["expected_status"],
            }
        )
        previous_action, previous_status = action, status
        # Promote explicit date/location answers into the simulated state.
        dates = re.search(
            r"(\d{1,2})月\s*(\d{1,2})日(?:到|至|—|-)?\s*(?:(\d{1,2})月)?\s*(\d{1,2})日?",
            turn["user"],
        )
        if dates and state.get("trip_dates") is None:
            first_month, first_day, second_month, second_day = dates.groups()
            state["trip_dates"] = [
                f"2026-{int(first_month):02d}-{int(first_day):02d}",
                f"2026-{int(second_month or first_month):02d}-{int(second_day):02d}",
            ]
        if state.get("destination") is None and _contains(turn["user"], "去成都"):
            state["destination"] = "成都"
    return {
        "turns": predictions,
        "tool_calls": tool_calls,
        "policy_violations": violations,
        "action_matches": sum(item["expected_action_match"] for item in predictions),
        "status_matches": sum(item["expected_status_match"] for item in predictions),
    }


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    contract = validate_dataset(dataset)
    group_results: dict[str, list[dict[str, Any]]] = {
        "B1_FIXED_POLICY": [],
        "B2_OBSERVATION_POLICY": [],
        "B3_GUARDED_POLICY": [],
    }
    family_scores: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    owner_scores: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    case_reports = []
    for case in dataset["cases"]:
        b1 = _run_policy(case, variant="B1_FIXED_POLICY")
        b2 = _run_policy(case, variant="B2_OBSERVATION_POLICY")
        b3 = _run_policy(case, variant="B3_GUARDED_POLICY")
        for name, result in zip(group_results, (b1, b2, b3), strict=True):
            group_results[name].append(result)
            family_scores[name][case["family"]] += result["action_matches"]
            for turn, prediction in zip(case["turns"], result["turns"], strict=True):
                owner = turn["action_owner"]
                owner_scores[name][owner]["turns"] += 1
                owner_scores[name][owner]["matches"] += int(prediction["expected_action_match"])
        case_reports.append(
            {
                "case_id": case["id"],
                "family": case["family"],
                "B1": b1,
                "B2": b2,
                "B3": b3,
            }
        )
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    scores = {}
    for name, results in group_results.items():
        total_turns = sum(len(item["turns"]) for item in results)
        actions = sum(item["action_matches"] for item in results)
        statuses = sum(item["status_matches"] for item in results)
        scores[name] = {
            "case_count": len(results),
            "turn_count": total_turns,
            "action_matches": actions,
            "action_match_rate": round(actions / total_turns, 4) if total_turns else 0,
            "status_matches": statuses,
            "status_match_rate": round(statuses / total_turns, 4) if total_turns else 0,
            "simulated_tool_calls": sum(len(item["tool_calls"]) for item in results),
            "policy_violations": [v for item in results for v in item["policy_violations"]],
            "family_action_matches": dict(sorted(family_scores[name].items())),
            "owner_action_scores": {
                owner: {
                    "turns": values["turns"],
                    "matches": values["matches"],
                    "match_rate": round(values["matches"] / values["turns"], 4),
                }
                for owner, values in sorted(owner_scores[name].items())
            },
        }
    return {
        "evaluation_id": f"{contract['evaluation_id']}_scripted",
        "status": "SYNTHETIC_SCRIPTED_POLICY_REPLAY",
        "dataset_id": contract["evaluation_id"],
        "provider_mode": "NO_LLM_NO_EXTERNAL_PROVIDERS",
        "case_count": contract["case_count"],
        "turn_count": contract["turn_count"],
        "case_file_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "groups": scores,
        "cases": case_reports,
        "source_provenance": evaluation_provenance("advisor_multiturn_task_scripted"),
        "interpretation": (
            "Deterministic rule-policy simulation over synthetic task states. It does not run "
            "the production Agent or measure LLM quality. B1 is a synthetic fixed-workflow "
            "approximation, not the historical production baseline; "
            "B2 adapts using task state and prior simulated outcomes; B3 adds the tool allowlist "
            "and explicit-approval gate. "
            "The B2 rules were authored and refined against this frozen suite, so the score is "
            "in-sample diagnostic evidence, not a held-out generalization estimate. "
            "No network requests or database writes are performed."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_dataset(dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"cases", "source_provenance"}
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
