"""Run a small, explicitly enabled real-LLM Skill ablation on synthetic text."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError
from travelpilot_api.settings import settings
from travelpilot_core.agents.extraction import extract_places
from travelpilot_core.skills import load_skill

CASES = (
    {
        "case_id": "chengdu-guide-two-explicit-places",
        "text": "这份成都攻略提到宽窄巷子和锦里，说锦里晚上适合散步。",
        "expected_names": ["宽窄巷子", "锦里"],
    },
    {
        "case_id": "chengdu-guide-tea-and-culture",
        "text": "我收藏的攻略写着：人民公园适合喝茶，武侯祠值得了解。",
        "expected_names": ["人民公园", "武侯祠"],
    },
    {
        "case_id": "guide-without-place-names",
        "text": "朋友写道：这里好美，值得再来，下次想早点出发。",
        "expected_names": [],
    },
)


def _score(case: dict[str, Any], places: list[dict[str, Any]]) -> dict[str, Any]:
    expected = set(case["expected_names"])
    names = {item["name"] for item in places}
    true_positive = expected & names
    evidence_count = sum(item["evidence_valid"] for item in places)
    grounded_names = sum(item["name_in_source"] for item in places)
    return {
        "expected_count": len(expected),
        "extracted_count": len(names),
        "true_positive_count": len(true_positive),
        "missed_expected_count": len(expected - names),
        "unexpected_count": len(names - expected),
        "verbatim_evidence_count": evidence_count,
        "name_in_source_count": grounded_names,
        "precision": len(true_positive) / len(names) if names else (1.0 if not expected else 0.0),
        "recall": len(true_positive) / len(expected) if expected else (1.0 if not names else 0.0),
        "places": places,
    }


def _safe_failure_details(exc: RuntimeError) -> dict[str, Any]:
    cause = exc.__cause__
    response = getattr(cause, "response", None)
    details: dict[str, Any] = {
        "error_code": str(exc),
        "cause_type": type(cause).__name__ if cause is not None else None,
        "http_status": getattr(response, "status_code", None),
    }
    response_shape = getattr(exc, "response_shape", None)
    if isinstance(response_shape, dict):
        details["response_shape"] = response_shape
    provider_metadata = getattr(exc, "provider_metadata", None)
    if isinstance(provider_metadata, dict):
        details["provider_metadata"] = provider_metadata
        details["http_status"] = provider_metadata.get("http_status", details["http_status"])
        if provider_metadata.get("finish_reason") == "length":
            details["error_code"] = "MODEL_OUTPUT_TRUNCATED"
    if isinstance(cause, ValidationError):
        details["validation_issues"] = [
            {
                "type": str(item.get("type", "unknown")),
                "location": [str(part) for part in item.get("loc", ())],
            }
            for item in cause.errors(include_input=False)[:8]
        ]
    return details


def _usage_count(result: dict[str, Any], kind: str) -> int | None:
    field = f"{kind}_tokens"
    value = result.get(field)
    if type(value) is int and value >= 0:
        return value
    usage_key = "prompt_tokens" if kind == "input" else "completion_tokens"
    usage = (result.get("provider_metadata") or {}).get("usage", {})
    value = usage.get(usage_key) if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


async def run_live_ablation(
    *, per_call_output_cap: int, thinking_mode: str | None = None,
    evaluation_id: str = "content_import_skill_live_ablation",
) -> dict[str, Any]:
    import asyncio

    if not settings.llm_api_key or not settings.llm_model:
        raise RuntimeError("LLM_NOT_CONFIGURED")
    skill = load_skill("content-import")
    results = []
    request_count = 0
    for case in CASES:
        for enabled in (False, True):
            if request_count:
                await asyncio.sleep(2)
            request_count += 1
            started_at = perf_counter()
            try:
                extraction = await extract_places(
                    case["text"], api_key=settings.llm_api_key, model=settings.llm_model,
                    base_url=settings.llm_base_url, token_budget=per_call_output_cap,
                    additional_instructions=skill.instructions if enabled else "",
                    thinking_mode=thinking_mode,
                )
                places = [{
                    "name": item.name,
                    "category": item.category,
                    "action": item.action,
                    "evidence_valid": bool(item.evidence and item.evidence in case["text"]),
                    "name_in_source": item.name.casefold() in case["text"].casefold(),
                } for item in extraction.places]
                results.append({
                    "case_id": case["case_id"],
                    "skill_enabled": enabled,
                    "skill_version": skill.version if enabled else None,
                    "status": "SUCCEEDED",
                    "latency_ms": round((perf_counter() - started_at) * 1000, 2),
                    "http_status": extraction.provider_http_status,
                    "finish_reason": extraction.finish_reason,
                    "input_tokens": max(0, extraction.token_usage - extraction.output_tokens),
                    "output_tokens": extraction.output_tokens,
                    "total_tokens": extraction.token_usage,
                    **_score(case, places),
                })
            except RuntimeError as exc:
                results.append({
                    "case_id": case["case_id"],
                    "skill_enabled": enabled,
                    "skill_version": skill.version if enabled else None,
                    "status": "FAILED",
                    "latency_ms": round((perf_counter() - started_at) * 1000, 2),
                    **_safe_failure_details(exc),
                })
    paired = []
    for case in CASES:
        baseline = next(row for row in results if row["case_id"] == case["case_id"] and not row["skill_enabled"])
        treatment = next(row for row in results if row["case_id"] == case["case_id"] and row["skill_enabled"])
        paired.append({
            "case_id": case["case_id"],
            "expected_place_count": len(case["expected_names"]),
            "has_expected_places": bool(case["expected_names"]),
            "both_succeeded": baseline["status"] == treatment["status"] == "SUCCEEDED",
            "baseline_recall": baseline.get("recall"),
            "skill_recall": treatment.get("recall"),
            "baseline_precision": baseline.get("precision"),
            "skill_precision": treatment.get("precision"),
            "baseline_evidence_count": baseline.get("verbatim_evidence_count"),
            "skill_evidence_count": treatment.get("verbatim_evidence_count"),
            "baseline_unexpected_count": baseline.get("unexpected_count"),
            "skill_unexpected_count": treatment.get("unexpected_count"),
            "baseline_input_tokens": _usage_count(baseline, "input"),
            "skill_input_tokens": _usage_count(treatment, "input"),
            "baseline_output_tokens": _usage_count(baseline, "output"),
            "skill_output_tokens": _usage_count(treatment, "output"),
        })
        baseline_token_counts = [
            paired[-1]["baseline_input_tokens"], paired[-1]["baseline_output_tokens"],
        ]
        skill_token_counts = [
            paired[-1]["skill_input_tokens"], paired[-1]["skill_output_tokens"],
        ]
        paired[-1]["baseline_total_tokens"] = (
            sum(baseline_token_counts) if all(value is not None for value in baseline_token_counts) else None
        )
        paired[-1]["skill_total_tokens"] = (
            sum(skill_token_counts) if all(value is not None for value in skill_token_counts) else None
        )
        baseline_total, skill_total = paired[-1]["baseline_total_tokens"], paired[-1]["skill_total_tokens"]
        paired[-1]["delta_total_tokens"] = (
            skill_total - baseline_total if baseline_total is not None and skill_total is not None else None
        )
    token_totals = {}
    for enabled, label in ((False, "baseline"), (True, "skill")):
        for kind in ("input", "output"):
            counts = [
                _usage_count(row, kind) for row in results if row["skill_enabled"] is enabled
            ]
            token_totals[f"{label}_{kind}_tokens"] = sum(value for value in counts if value is not None)
            token_totals[f"{label}_{kind}_recorded_calls"] = sum(value is not None for value in counts)
    token_totals["baseline_total_tokens"] = (
        token_totals["baseline_input_tokens"] + token_totals["baseline_output_tokens"]
    )
    token_totals["skill_total_tokens"] = token_totals["skill_input_tokens"] + token_totals["skill_output_tokens"]
    token_totals["delta_total_tokens"] = token_totals["skill_total_tokens"] - token_totals["baseline_total_tokens"]
    return {
        "evaluation_id": evaluation_id,
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "provider_mode": "CONFIGURED_LLM",
        "skill": {"name": skill.name, "version": skill.version},
        "case_count": len(CASES),
        "request_count": len(results),
        "per_call_output_cap": per_call_output_cap,
        "thinking_mode": thinking_mode or "provider_default",
        "maximum_generated_tokens": len(results) * per_call_output_cap,
        "actual_generated_tokens": sum(
            row.get("output_tokens", (row.get("provider_metadata") or {}).get("usage", {}).get("completion_tokens", 0))
            for row in results
        ),
        "scope": "small synthetic content-import prompt ablation; exploratory only, not a quality guarantee",
        "results": results,
        "paired_comparison": paired,
        "token_totals": token_totals,
        "successful_pair_count": sum(item["both_succeeded"] for item in paired),
        "successful_positive_case_pair_count": sum(
            item["both_succeeded"] and item["has_expected_places"] for item in paired
        ),
    }


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live-requests", action="store_true",
                        help="explicitly allow requests to the configured model endpoint")
    parser.add_argument("--max-output-tokens", type=int, default=160)
    parser.add_argument("--thinking-mode", choices=("provider-default", "enabled", "disabled"),
                        default="provider-default",
                        help="optionally set the OpenAI-compatible thinking mode")
    parser.add_argument("--output", type=Path,
                        default=Path("tests/evaluation/results/content_import_skill_live_ablation_v5.json"))
    args = parser.parse_args()
    if not args.confirm_live_requests:
        parser.error("pass --confirm-live-requests to run the bounded external model calls")
    if not 1 <= args.max_output_tokens <= 256:
        parser.error("--max-output-tokens must be between 1 and 256")
    report = asyncio.run(run_live_ablation(
        per_call_output_cap=args.max_output_tokens,
        thinking_mode=None if args.thinking_mode == "provider-default" else args.thinking_mode,
        evaluation_id=args.output.stem,
    ))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({
        "evaluation_id": report["evaluation_id"],
        "model": report["model"],
        "request_count": report["request_count"],
        "max_generated_tokens": report["maximum_generated_tokens"],
        "statuses": [row["status"] for row in report["results"]],
        "output": str(args.output),
    }, ensure_ascii=False))
