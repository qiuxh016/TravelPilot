"""Prepare a reviewer packet from a completed corrected-source product run.

The packet deliberately omits the model name, tool trace, expected checks and
pass/fail labels. It is a single-system human quality review, not a comparison.
"""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "evaluation" / "results"
REVIEWS = ROOT / "tests" / "evaluation" / "reviews"
SCENARIOS = (
    "schedule-two-turn",
    "schedule-route-weather",
    "schedule-proposal-edit",
    "lunch-only",
    "lunch-weather-preparation",
    "lunch-weather-weather-outage",
    "mixed-attraction-lunch",
    "mixed-attraction-lunch-draft",
    "lodging-only",
    "general-trip-question",
    "weather-preparation",
    "route-weather-preparation",
    "route-weather-route-outage",
    "route-weather-weather-outage",
    "weather-indoor-search",
    "route-provider-outage",
)
SCORE_FIELDS = (
    "factual_grounding",
    "constraint_safety",
    "task_progress",
    "clarification_quality",
    "transparency",
)


def _evidence(turn: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    return {
        "trip_context": turn["trip_context"],
        "evidence_mode": {
            "maps": report["maps_data_mode"],
            "weather": report["weather_data_mode"],
        },
        "verified_candidates": turn.get("verified_candidates", []),
        "weather_evidence": turn.get("weather_evidence", {}),
        "route_evidence": turn.get("route_evidence", {}),
        "schedule_assignments": turn.get("schedule_assignments", []),
        "schedule_breaks": turn.get("schedule_breaks", []),
    }


def _load_conversation_path(path: Path, scenario: str) -> list[dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("provider_mode") != "LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS"
        or report.get("scenario_id") != scenario
        or report.get("failure") is not None
        or report.get("product_assessment", {}).get("passed") is not True
        or report.get("temporary_account_deleted") is not True
        or report.get("resources", {}).get("formal_write_prevented") is not True
        or not report.get("turns")
    ):
        raise ValueError(f"{path.name} is not a completed, clean product evaluation")

    conversation = []
    for turn in report["turns"]:
        if (
            turn.get("http_status") != 200
            or turn.get("assistant_reply_complete") is not True
            or not isinstance(turn.get("assistant_reply"), str)
            or not turn["assistant_reply"].strip()
        ):
            raise ValueError(f"{path.name} contains an incomplete response")
        conversation.append({
            "user_message": turn["user_message"],
            "available_context_and_evidence": _evidence(turn, report),
            "advisor_reply": turn["assistant_reply"],
        })
    return conversation


def _scenario_paths_from_manifest(manifest_path: Path) -> dict[str, Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_paths = manifest.get("scenario_reports")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("aggregate manifest must contain scenario_reports")
    paths: dict[str, Path] = {}
    results_root = RESULTS.resolve()
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            raise TypeError("scenario report paths must be strings")
        path = Path(raw_path).resolve()
        if path.parent != results_root or not path.is_file():
            raise ValueError("scenario reports must be existing files directly under evaluation results")
        report = json.loads(path.read_text(encoding="utf-8"))
        scenario = report.get("scenario_id")
        if not isinstance(scenario, str) or scenario not in SCENARIOS or scenario in paths:
            raise ValueError("manifest scenario reports must uniquely cover the frozen review scenarios")
        paths[scenario] = path
    if set(paths) != set(SCENARIOS):
        raise ValueError("manifest must cover every frozen review scenario exactly once")
    return paths


def _source_run_date(scenario_paths: dict[str, Path]) -> str:
    """Return the shared run date without exposing provider/model metadata."""
    dates: set[str] = set()
    for path in scenario_paths.values():
        report = json.loads(path.read_text(encoding="utf-8"))
        raw = report.get("run_at_utc")
        if not isinstance(raw, str) or len(raw) < 10:
            continue
        candidate = raw[:10].replace("/", "-")
        try:
            dates.add(date.fromisoformat(candidate).isoformat())
        except ValueError:
            continue
    if len(dates) > 1:
        raise ValueError("review scenarios must come from one evaluation run date")
    return next(iter(dates), "date unavailable")


def build_review_packet(
    prefix: str | None = None,
    *,
    scenario_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    if (prefix is None) == (scenario_paths is None):
        raise ValueError("provide exactly one report prefix or scenario_paths mapping")
    if prefix is not None and (not prefix or Path(prefix).name != prefix):
        raise ValueError("prefix must be a plain report filename prefix")
    if scenario_paths is not None and set(scenario_paths) != set(SCENARIOS):
        raise ValueError("scenario_paths must cover every frozen review scenario exactly once")
    source_paths = scenario_paths or {
        scenario: RESULTS / f"{prefix}_{scenario}.json"
        for scenario in SCENARIOS
    }
    source_date = _source_run_date(source_paths)
    responses = []
    for scenario in SCENARIOS:
        path = (
            scenario_paths[scenario]
            if scenario_paths is not None
            else RESULTS / f"{prefix}_{scenario}.json"
        )
        responses.append({
            "review_id": secrets.token_hex(8),
            "conversation": _load_conversation_path(path, scenario),
            "scores": {**dict.fromkeys(SCORE_FIELDS), "reviewer_notes": ""},
        })
    return {
        "review_protocol": "advisor_single_system_quality_review_v1",
        "assessment_type": "independent human quality review; one system; not a comparative test",
        "sample": {
            "conversation_count": len(responses),
            "turn_count": sum(len(item["conversation"]) for item in responses),
            "source": f"bounded live Advisor product run (UTC {source_date})",
        },
        "rubric": {
            "scale": (
                "1-5 integer; leave null until reviewed. Use 2 or 4 only when the response falls "
                "between the adjacent behavior anchors. Score each conversation as a whole."
            ),
            "dimensions": {
                "factual_grounding": "Uses only supplied context and evidence; avoids unsupported places, times, routes, weather, prices or availability.",
                "constraint_safety": "Respects explicit dates, preferences and task scope; distinguishes draft from saved itinerary.",
                "task_progress": "Moves the stated travel task forward with a relevant result or concrete next step.",
                "clarification_quality": "Asks focused questions when material information is missing, without unnecessary interrogation.",
                "transparency": "Distinguishes verified, simulated and uncertain information and explains relevant failures.",
            },
            "anchors": {
                "factual_grounding": {
                    "1": "重大事实没有上下文或证据支持，或把模拟数据说成真实信息。",
                    "3": "大部分内容有依据，但有重要推断未标明，或漏掉关键限定。",
                    "5": "所有关键事实均能对应已提供的上下文或证据；不确定处标注清楚。",
                },
                "constraint_safety": {
                    "1": "忽视明确限制、用户任务范围或确认边界，可能误导用户认为已正式保存。",
                    "3": "遵守主要要求，但漏掉一项重要限制或草案状态说明。",
                    "5": "完整遵守明确要求与任务范围；清楚区分建议、草案和正式保存。",
                },
                "task_progress": {
                    "1": "没有推动用户目标，或主要内容与请求无关。",
                    "3": "部分推进目标，但遗漏关键步骤或只给出较笼统的下一步。",
                    "5": "完成当前可支持的目标；若无法完成，给出具体且有用的下一步。",
                },
                "clarification_quality": {
                    "1": "遗漏会改变结果的关键信息，或反复询问已知内容、提出无关问题。",
                    "3": "识别了部分不确定项，但问题过宽、过多，或遗漏一个重要澄清点。",
                    "5": "只在必要时追问，问题聚焦关键决策，并利用已有上下文避免重复询问。",
                },
                "transparency": {
                    "1": "将未核验、模拟或失败结果表述为确定事实，或隐瞒会影响决策的限制。",
                    "3": "标出部分来源或状态，但重要时效、局限或失败影响说明不足。",
                    "5": "清晰说明来源、核验状态、不确定性及相关故障，并说明其对建议的影响。",
                },
            },
            "reviewer_instructions": (
                "Read the whole conversation in order. Score each dimension independently using only the shown user "
                "messages, context, evidence and replies. Evidence is synthetic where marked; do not treat it as "
                "real-world travel data. Do not infer or guess the model identity. Use the 1/3/5 anchors and choose "
                "2 or 4 only for intermediate performance. Record concrete omissions or unsupported claims in "
                "reviewer_notes."
            ),
        },
        "responses": responses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default="advisor_product_live_full_v2_20261019",
        help="completed per-scenario result filename prefix",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="aggregate report whose scenario_reports may come from multiple run prefixes",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REVIEWS / "advisor_product_quality_review_corrected_20261019.json",
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite existing review packet: {args.output}")
    if args.manifest is not None:
        packet = build_review_packet(scenario_paths=_scenario_paths_from_manifest(args.manifest))
    else:
        packet = build_review_packet(args.prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"conversation_count": packet["sample"]["conversation_count"],
                      "turn_count": packet["sample"]["turn_count"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
