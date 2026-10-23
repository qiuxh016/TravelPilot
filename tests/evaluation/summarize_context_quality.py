"""Create a compact, reproducible analysis of the frozen live context evaluation."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "tests/evaluation/results/advisor_context_quality_v1_live.json"
DATASET = ROOT / "tests/evaluation/cases/advisor_context_quality_v1.json"
OUTPUT = ROOT / "tests/evaluation/results/advisor_context_quality_v1_analysis.json"
ARMS = ("RECENT_HISTORY", "STRUCTURED_CONTEXT", "SELECTIVE_CONTEXT")
FIXTURE_CAVEAT = "ctxq-v1-12-preserve-lodging"
LABEL_CAVEAT = "ctxq-v1-15-cancel-pending"


def _case_metrics(summary: dict[str, Any], case_ids: set[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for arm in ARMS:
        rows = [row for row in summary[arm]["runs"] if row["case_id"] in case_ids]
        decisions = [turn for row in rows for turn in row["turns"]]
        result[arm] = {
            "cases": len(rows),
            "decisions": len(decisions),
            "action_matches": sum(turn.get("expected_action_match") is True for turn in decisions),
            "action_match_rate": round(
                sum(turn.get("expected_action_match") is True for turn in decisions) / max(1, len(decisions)), 4
            ),
            "complete_sequences": sum(row.get("sequence_match") is True for row in rows),
            "no_valid_action_responses": sum(turn.get("action") is None for turn in decisions),
            "invalid_returned_actions": sum(
                turn.get("action") is not None and turn.get("action_valid") is False for turn in decisions
            ),
            "unallowlisted_tool_calls": sum(
                isinstance(turn.get("action"), dict)
                and turn["action"].get("kind") == "CALL_TOOL"
                and turn.get("allowlist_valid") is False
                for turn in decisions
            ),
        }
    return result


def _paired_case_deltas(summary: dict[str, Any], case_ids: set[str], metric: str) -> list[float]:
    per_arm = {}
    for arm in ARMS:
        per_arm[arm] = {
            row["case_id"]: (
                float(row.get("sequence_match") is True)
                if metric == "sequence"
                else sum(turn.get("expected_action_match") is True for turn in row["turns"]) / len(row["turns"])
            )
            for row in summary[arm]["runs"] if row["case_id"] in case_ids
        }
    return [per_arm["SELECTIVE_CONTEXT"][case_id] - per_arm["RECENT_HISTORY"][case_id]
            for case_id in sorted(case_ids)]


def _bootstrap_mean_ci(values: list[float], *, seed: int = 20261023, samples: int = 20000) -> list[float]:
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(samples))
    return [round(means[int(samples * 0.025)], 4), round(means[int(samples * 0.975)], 4)]


def _exact_mcnemar(discordant_wins: int, discordant_losses: int) -> float:
    total = discordant_wins + discordant_losses
    if total == 0:
        return 1.0
    tail = sum(__import__("math").comb(total, k) for k in range(min(discordant_wins, discordant_losses) + 1))
    return min(1.0, 2 * tail / (2**total))


def build_analysis(raw: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    summary = raw["summary"]
    all_ids = {case["id"] for case in dataset["cases"]}
    fixture_adjusted = all_ids - {FIXTURE_CAVEAT}
    label_adjusted = all_ids - {LABEL_CAVEAT}
    all_metrics = _case_metrics(summary, all_ids)
    fixture_metrics = _case_metrics(summary, fixture_adjusted)
    label_metrics = _case_metrics(summary, label_adjusted)

    sequence_deltas = _paired_case_deltas(summary, all_ids, "sequence")
    action_deltas = _paired_case_deltas(summary, all_ids, "action")
    wins = sum(delta > 0 for delta in sequence_deltas)
    losses = sum(delta < 0 for delta in sequence_deltas)
    ties = len(sequence_deltas) - wins - losses
    invalid_outputs = {
        arm: sum(
            turn.get("action") is None
            for case in summary[arm]["runs"] for turn in case["turns"]
        )
        for arm in ARMS
    }
    return {
        "evaluation_id": raw["evaluation_id"],
        "dataset_sha256": raw["dataset_sha256"],
        "model": raw["model"],
        "requests_used": raw["requests_used"],
        "prior_unrecorded_requests": raw["prior_unrecorded_requests"],
        "cumulative_request_upper_bound": raw["cumulative_request_upper_bound"],
        "output_tokens_reported": raw["output_tokens_reported"],
        "prior_output_token_reserve": raw["prior_output_token_reserve"],
        "cumulative_output_token_upper_bound": raw["cumulative_output_token_upper_bound"],
        "authorization_ceiling": raw["authorization_ceiling"],
        "analysis_method": {
            "paired_unit": "case",
            "action_ci": "20,000 paired case bootstrap resamples, fixed seed 20261023; each case contributes its 2-turn match rate",
            "sequence_test": "two-sided exact McNemar on paired case success/failure",
            "caveat": "Exploratory small synthetic set; action labels are not a human blind rating of final response quality.",
        },
        "all_cases": all_metrics,
        "selective_vs_baseline": {
            "action_match_rate_delta": round(all_metrics["SELECTIVE_CONTEXT"]["action_match_rate"] - all_metrics["RECENT_HISTORY"]["action_match_rate"], 4),
            "paired_action_rate_delta_95_ci": _bootstrap_mean_ci(action_deltas),
            "complete_sequence_wins": wins,
            "complete_sequence_losses": losses,
            "complete_sequence_ties": ties,
            "complete_sequence_delta": round((wins - losses) / len(sequence_deltas), 4),
            "exact_mcnemar_p_two_sided": round(_exact_mcnemar(wins, losses), 6),
        },
        "fixture_sensitivity_excluding_coordinate_mismatch_case": {
            "excluded_case": FIXTURE_CAVEAT,
            "reason": "synthetic saved-place coordinates are Shanghai while destination is Guangzhou",
            "metrics": fixture_metrics,
        },
        "label_sensitivity_excluding_disputed_cancellation_case": {
            "excluded_case": LABEL_CAVEAT,
            "reason": "asking for suitable Yunnan places is plausibly a valid discovery/search task, so expected ASK_USER may be over-conservative",
            "metrics": label_metrics,
        },
        "response_integrity": {
            "no_valid_action_responses_by_arm": invalid_outputs,
            "unallowlisted_tool_calls_by_arm": {arm: all_metrics[arm]["unallowlisted_tool_calls"] for arm in ARMS},
            "metric_correction": "The raw allowlist_violations counter counted PlannerDecisionFailure with action=null as a violation. Use unallowlisted_tool_calls above; null actions are reported separately.",
        },
        "decision": "No production context-policy change: selective context has a small action-level difference, no net complete-sequence gain, and the paired set is too small and has adjudication/fixture caveats to establish a reliable improvement.",
    }


def main() -> None:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    OUTPUT.write_text(json.dumps(build_analysis(raw, dataset), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
