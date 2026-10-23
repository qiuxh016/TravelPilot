"""Prepare a blind, exploratory review of two real Advisor API iterations.

This is a small UX comparison, not the planned B1/B2/B3 model experiment. It
uses complete synthetic-trip replies already captured by the product API runner
and does not call a model, provider, or application endpoint.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from travelpilot_api.build_info import fingerprint_manifest

from tests.evaluation.advisor_product_live_eval import SCENARIOS, TURN_INPUTS
from tests.evaluation.blind_review import build_blind_review

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests/evaluation/results"
FIRST = RESULTS / "advisor_product_live_api_claim_validation_20261019.json"
SECOND = RESULTS / "advisor_product_live_api_schedule_fix_20261019.json"
ARMS = ("iteration_a", "iteration_b")
MATCHED_RUN_CONFIG_FIELDS = (
    "base_url_origin",
    "server_output_cap_per_turn",
    "server_session_output_cap",
    "runner_process_output_cap_per_turn",
    "runner_process_session_output_cap",
)
MATCHED_SCENARIO_CONFIG_FIELDS = tuple(
    field for field in MATCHED_RUN_CONFIG_FIELDS if field != "base_url_origin"
)


def _load_complete_replies(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    turns = report.get("turns")
    if (
        report.get("evaluation_id") != "advisor_product_live_api_two_turn_v1"
        or not str(report.get("provider_mode", "")).startswith("LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS")
        or not isinstance(report.get("model"), str)
        or not report["model"].strip()
        or report.get("temporary_account_deleted") is not True
        or report.get("failure") is not None
        or report.get("resources", {}).get("formal_write_prevented") is not True
        or not isinstance(report.get("api_runtime_source_sha256"), dict)
        or not report["api_runtime_source_sha256"]
        or report.get("api_runtime_fingerprint") != fingerprint_manifest(
            report["api_runtime_source_sha256"]
        )
        or not isinstance(turns, list)
        or len(turns) != len(TURN_INPUTS)
        or [turn.get("turn") for turn in turns] != [1, 2]
    ):
        raise ValueError(f"{path.name} is not a completed, matching product API run")
    source_hashes = report.get("source_provenance", {}).get("source_sha256", {})
    if not source_hashes.get("tests/evaluation/advisor_product_live_eval.py"):
        raise ValueError(f"{path.name} does not pin the product-evaluation input source")
    for turn in turns:
        reply = turn.get("assistant_reply_excerpt")
        if (
            turn.get("http_status") != 200
            or turn.get("assistant_reply_present") is not True
            or not isinstance(reply, str)
            or not reply.strip()
            or turn.get("assistant_reply_complete") is not True
            or not isinstance(turn.get("assistant_reply"), str)
            or not turn["assistant_reply"].strip()
            or len(turn["assistant_reply"]) > 1600
            or not isinstance(turn.get("user_message"), str)
            or not isinstance(turn.get("trip_context"), dict)
        ):
            raise ValueError(f"{path.name} has a failed, missing, or possibly truncated reply")
    return report


def prepare_review(first_path: Path, second_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    first, second = _load_complete_replies(first_path), _load_complete_replies(second_path)
    if first_path.resolve() == second_path.resolve():
        raise ValueError("iteration reports must be different files")
    if first.get("model") != second.get("model"):
        raise ValueError("paired product iterations must use the same model")
    if any(
        first.get(field) is None or second.get(field) is None
        for field in MATCHED_RUN_CONFIG_FIELDS
    ):
        raise ValueError("paired product runs are missing matched model/runtime settings")
    if any(first[field] != second[field] for field in MATCHED_RUN_CONFIG_FIELDS):
        raise ValueError("paired product runs used different model/runtime settings")
    first_hash = first["source_provenance"]["source_sha256"][
        "tests/evaluation/advisor_product_live_eval.py"
    ]
    second_hash = second["source_provenance"]["source_sha256"][
        "tests/evaluation/advisor_product_live_eval.py"
    ]
    if first_hash != second_hash:
        raise ValueError("product evaluator input definitions changed between runs")
    first_product_sources = first["api_runtime_source_sha256"]
    second_product_sources = second["api_runtime_source_sha256"]
    if not first_product_sources or set(first_product_sources) != set(second_product_sources):
        raise ValueError("paired product reports must pin the same application source files")
    changed_sources = [
        name for name, digest in first_product_sources.items()
        if second_product_sources[name] != digest
    ]
    if not changed_sources:
        raise ValueError("paired product iterations have no application-code difference")

    first_turns, second_turns = first["turns"], second["turns"]
    for left, right in zip(first_turns, second_turns, strict=True):
        if left["user_message"] != right["user_message"]:
            raise ValueError("paired product runs used different user messages")
        if left["trip_context"] != right["trip_context"]:
            raise ValueError("paired product runs used different trip contexts")
    if any(turn["user_message"] != TURN_INPUTS[index] for index, turn in enumerate(first_turns)):
        raise ValueError("product report inputs differ from the frozen evaluation prompts")

    records = []
    for arm, report in zip(ARMS, (first, second), strict=True):
        turns = report["turns"]
        records.append({
            "case_id": "synthetic_chengdu_itinerary_iteration",
            "turn": 1,
            "arm": arm,
            "user_goal": "\n".join(
                f"第{turn['turn']}轮用户：{turn['user_message']}" for turn in turns
            ),
            "trip_context": json.dumps(
                [
                    {"turn": turn["turn"], "context": turn["trip_context"]}
                    for turn in turns
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "response": "\n\n".join(
                f"第{turn['turn']}轮顾问：{turn['assistant_reply']}" for turn in turns
            ),
        })
    if any(len(record["response"]) > 1600 for record in records):
        raise ValueError("complete paired conversation exceeds blind-review text limit")
    sheet, key = build_blind_review(records, arms=ARMS)
    key["changed_application_sources"] = changed_sources
    key["api_runtime_fingerprint_by_arm"] = {
        ARMS[0]: first["api_runtime_fingerprint"],
        ARMS[1]: second["api_runtime_fingerprint"],
    }
    key["application_source_sha256_by_arm"] = {
        ARMS[0]: first_product_sources,
        ARMS[1]: second_product_sources,
    }
    return sheet, key


def prepare_scenario_review(
    first_path: Path, second_path: Path, scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Blindly pair complete turns from two runs of the same live scenario."""
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown product scenario: {scenario_id}")
    if first_path.resolve() == second_path.resolve():
        raise ValueError("scenario reports must be different files")
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    expected_id = f"advisor_product_api_{scenario_id}_v1"
    for report, path in ((first, first_path), (second, second_path)):
        turns = report.get("turns")
        if (
            report.get("evaluation_id") != expected_id
            or report.get("scenario_id") != scenario_id
            or not str(report.get("provider_mode", "")).startswith("LIVE_MODEL_REAL_ADVISOR_API_FAKE_MAPS")
            or not isinstance(report.get("model"), str) or not report["model"].strip()
            or report.get("temporary_account_deleted") is not True
            or report.get("failure") is not None
            or report.get("resources", {}).get("formal_write_prevented") is not True
            or not isinstance(report.get("api_runtime_source_sha256"), dict)
            or not report["api_runtime_source_sha256"]
            or not isinstance(turns, list)
            or len(turns) != len(SCENARIOS[scenario_id]["turn_inputs"])
        ):
            raise ValueError(f"{path.name} is not a completed, safe live scenario run")
        for index, turn in enumerate(turns, 1):
            reply = turn.get("assistant_reply")
            if (
                turn.get("turn") != index or turn.get("http_status") != 200
                or turn.get("assistant_reply_present") is not True
                or turn.get("assistant_reply_complete") is not True
                or not isinstance(reply, str) or not reply.strip() or len(reply) > 1600
                or not isinstance(turn.get("user_message"), str)
                or not isinstance(turn.get("trip_context"), dict)
            ):
                raise ValueError(f"{path.name} has a failed, missing, or truncated reply")

    if first.get("model") != second.get("model"):
        raise ValueError("paired scenario runs must use the same model")
    for field in MATCHED_SCENARIO_CONFIG_FIELDS:
        left, right = first.get(field), second.get(field)
        if left is None or right is None or left != right:
            raise ValueError("paired scenario runs must use matching provider and token settings")
    first_turns, second_turns = first["turns"], second["turns"]
    for left, right in zip(first_turns, second_turns, strict=True):
        if left["user_message"] != right["user_message"]:
            raise ValueError("paired scenario runs used different user messages")
        if left["trip_context"] != right["trip_context"]:
            raise ValueError("paired scenario runs used different trip contexts")

    records = []
    for turn_index, (left, right) in enumerate(zip(first_turns, second_turns, strict=True), 1):
        for arm, report, turn in zip(ARMS, (first, second), (left, right), strict=True):
            records.append({
                "case_id": scenario_id,
                "turn": turn_index,
                "arm": arm,
                "user_goal": "\n".join(
                    f"第{item['turn']}轮用户：{item['user_message']}"
                    for item in first_turns[:turn_index]
                ),
                "trip_context": json.dumps(
                    [item["trip_context"] for item in first_turns[:turn_index]],
                    ensure_ascii=False, sort_keys=True,
                ),
                "response": "\n\n".join(
                    f"第{item['turn']}轮顾问：{item['assistant_reply']}"
                    for item in report["turns"][:turn_index]
                ),
            })
    sheet, key = build_blind_review(records, arms=ARMS)
    key.update({
        "scenario_id": scenario_id,
        "model": first["model"],
        "api_runtime_fingerprint_by_arm": {
            ARMS[0]: first.get("api_runtime_fingerprint"),
            ARMS[1]: second.get("api_runtime_fingerprint"),
        },
        "application_source_sha256_by_arm": {
            ARMS[0]: first["api_runtime_source_sha256"],
            ARMS[1]: second["api_runtime_source_sha256"],
        },
    })
    return sheet, key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_sheet", type=Path)
    parser.add_argument("private_key", type=Path)
    parser.add_argument("--first", type=Path, default=FIRST)
    parser.add_argument("--second", type=Path, default=SECOND)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS))
    args = parser.parse_args()
    if args.review_sheet.resolve() == args.private_key.resolve():
        parser.error("review sheet and private key must use different paths")
    if args.review_sheet.exists() or args.private_key.exists():
        parser.error("refusing to overwrite an existing review artifact")
    if args.scenario:
        sheet, key = prepare_scenario_review(args.first, args.second, args.scenario)
    else:
        sheet, key = prepare_review(args.first, args.second)
    args.review_sheet.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.review_sheet.write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    args.private_key.write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "paired_case_turns": len(sheet["responses"]) // len(ARMS),
        "response_count": len(sheet["responses"]),
        "review_sheet": str(args.review_sheet),
        "private_key": str(args.private_key),
        "models_called": 0,
        "external_requests": 0,
    }))


if __name__ == "__main__":
    main()
