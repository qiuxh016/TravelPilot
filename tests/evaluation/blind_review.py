"""Build anonymized, paired review sheets for Advisor response comparisons."""

from __future__ import annotations

import argparse
import json
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

_MAX_RECORDS = 300
_MAX_TEXT = 1600
_RUBRIC = {
    "factual_grounding": None,
    "constraint_safety": None,
    "task_progress": None,
    "clarification_quality": None,
    "transparency": None,
    "reviewer_notes": "",
}
_SCORE_FIELDS = tuple(_RUBRIC)[:-1]
_RUBRIC_GUIDANCE = {
    "scale": {
        "1": "Poor: materially fails this dimension or creates a misleading impression.",
        "3": "Mixed: partially useful, but has a clear gap or needs user correction.",
        "5": "Strong: directly satisfies this dimension with appropriate evidence and care.",
    },
    "dimensions": {
        "factual_grounding": "Uses the supplied trip context and available evidence; does not invent places, times, routes, weather, prices or availability.",
        "constraint_safety": "Respects explicit dates, preferences and task scope; does not imply an unconfirmed plan was saved or make an unauthorized commitment.",
        "task_progress": "Moves the user's stated travel task forward with a relevant answer, useful candidate or concrete next step.",
        "clarification_quality": "Asks about material missing information when needed, with focused questions and no unnecessary interrogation.",
        "transparency": "Clearly distinguishes verified, simulated and uncertain information, and explains relevant limitations or failures.",
    },
    "reviewer_instructions": (
        "Rate each response independently using only the displayed user goal, trip context and response. "
        "Do not infer or guess which system produced it; do not reward verbosity by itself. "
        "Use reviewer_notes to identify concrete unsupported claims or important omissions."
    ),
}


def build_blind_review(
    records: list[dict[str, Any]],
    *,
    arms: tuple[str, ...] = ("B1_FIXED", "B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"),
    id_factory: Callable[[], str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a blinded scoring sheet and a separate arm-to-ID key.

    Only the user goal, bounded trip context and response cross the blind.
    Expected actions, system labels, tool traces, token counts and diagnostics
    are intentionally excluded so the reviewer cannot infer the arm from them.
    """
    if not records or len(records) > _MAX_RECORDS:
        raise ValueError(f"records must contain 1–{_MAX_RECORDS} paired responses")
    if len(arms) < 2 or len(set(arms)) != len(arms):
        raise ValueError("at least two distinct system arms are required")
    make_id = id_factory or (lambda: secrets.token_hex(8))
    normalized: list[dict[str, Any]] = []
    pairs: dict[tuple[str, int], set[str]] = {}
    paired_prompts: dict[tuple[str, int], tuple[str, str]] = {}
    for record in records:
        case_id, turn, arm = record.get("case_id"), record.get("turn"), record.get("arm")
        if (
            not isinstance(case_id, str) or not case_id or len(case_id) > 100
            or isinstance(turn, bool) or not isinstance(turn, int) or turn < 1
            or not isinstance(arm, str)
            or arm not in arms
        ):
            raise ValueError("each response needs a valid case_id, turn and known arm")
        pair_key = (case_id, turn)
        seen = pairs.setdefault(pair_key, set())
        if arm in seen:
            raise ValueError(f"duplicate response for {case_id} turn {turn} arm {arm}")
        seen.add(arm)
        values = {}
        for field in ("user_goal", "trip_context", "response"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError(f"{field} must be non-empty text of at most {_MAX_TEXT} characters")
            values[field] = value.strip()
        prompt = (values["user_goal"], values["trip_context"])
        if pair_key in paired_prompts and paired_prompts[pair_key] != prompt:
            raise ValueError("paired responses must use the same user goal and trip context")
        paired_prompts[pair_key] = prompt
        normalized.append({"case_id": case_id, "turn": turn, "arm": arm, **values})

    expected = set(arms)
    incomplete = [key for key, found in pairs.items() if found != expected]
    if incomplete:
        raise ValueError(f"paired cases are incomplete: {len(incomplete)} case-turn groups")

    shuffle = secrets.SystemRandom().shuffle
    shuffle(normalized)
    sheet_rows = []
    key_rows = []
    used_ids: set[str] = set()
    for record in normalized:
        review_id = make_id()
        if not isinstance(review_id, str) or not review_id or review_id in used_ids:
            raise ValueError("review IDs must be unique non-empty strings")
        used_ids.add(review_id)
        sheet_rows.append({
            "review_id": review_id,
            "case_id": record["case_id"],
            "turn": record["turn"],
            "user_goal": record["user_goal"],
            "trip_context": record["trip_context"],
            "response": record["response"],
            "scores": dict(_RUBRIC),
        })
        key_rows.append({
            "review_id": review_id,
            "case_id": record["case_id"],
            "turn": record["turn"],
            "arm": record["arm"],
        })

    return (
        {
            "review_protocol": "advisor_blind_review_v1",
            "score_scale": "1-5; see rubric anchors",
            "rubric": _RUBRIC_GUIDANCE,
            "responses": sheet_rows,
        },
        {"review_protocol": "advisor_blind_review_key_v1", "mapping": key_rows},
    )


def summarize_blind_review(sheet: dict[str, Any], key: dict[str, Any]) -> dict[str, Any]:
    """Validate completed ratings and calculate per-arm and paired means."""
    if sheet.get("review_protocol") != "advisor_blind_review_v1":
        raise ValueError("unsupported blind review sheet")
    if key.get("review_protocol") != "advisor_blind_review_key_v1":
        raise ValueError("unsupported private review key")
    rows = sheet.get("responses")
    mappings = key.get("mapping")
    if not isinstance(rows, list) or not rows or not isinstance(mappings, list):
        raise ValueError("sheet and key must contain response and mapping lists")
    by_id = {entry.get("review_id"): entry for entry in mappings if isinstance(entry, dict)}
    if len(by_id) != len(mappings) or set(by_id) != {
        row.get("review_id") for row in rows if isinstance(row, dict)
    }:
        raise ValueError("review IDs do not match between sheet and key")

    arm_values = [entry.get("arm") for entry in mappings]
    if any(not isinstance(arm, str) or not arm for arm in arm_values):
        raise ValueError("private key arm labels must be non-empty strings")
    arms = set(arm_values)
    if len(arms) < 2:
        raise ValueError("private key must map at least two system arms")
    values: dict[str, dict[str, list[int]]] = {
        arm: {field: [] for field in _SCORE_FIELDS} for arm in arms
    }
    paired: dict[tuple[str, int], dict[str, dict[str, int]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("each review response must be an object")
        mapping = by_id[row["review_id"]]
        if (row.get("case_id"), row.get("turn")) != (mapping.get("case_id"), mapping.get("turn")):
            raise ValueError("review key case/turn does not match the sheet")
        arm = mapping.get("arm")
        group = paired.setdefault((mapping["case_id"], mapping["turn"]), {})
        if arm in group:
            raise ValueError("duplicate arm in a paired case-turn group")
        scores = row.get("scores")
        if not isinstance(scores, dict):
            raise TypeError("each response needs a score object")
        group[arm] = {}
        for field in _SCORE_FIELDS:
            score = scores.get(field)
            if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
                raise ValueError(f"{field} must be an integer score from 1 to 5")
            values[arm][field].append(score)
            group[arm][field] = score

    expected_arms = set(arms)
    if any(set(group) != expected_arms for group in paired.values()):
        raise ValueError("review ratings are not paired across all system arms")
    per_arm = {
        arm: {
            field: round(sum(scores) / len(scores), 3)
            for field, scores in fields.items()
        }
        for arm, fields in values.items()
    }
    comparisons = {}
    ordered_arms = sorted(arms)
    for index, left in enumerate(ordered_arms):
        for right in ordered_arms[index + 1:]:
            comparisons[f"{right}_minus_{left}"] = {
                field: round(
                    sum(group[right][field] - group[left][field] for group in paired.values())
                    / len(paired),
                    3,
                )
                for field in _SCORE_FIELDS
            }
    return {
        "review_protocol": "advisor_blind_review_summary_v1",
        "paired_case_turns": len(paired),
        "responses": len(rows),
        "per_arm_mean": per_arm,
        "paired_mean_difference": comparisons,
        "interpretation": "Descriptive reviewer scores only; no statistical significance is inferred.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON array of bounded response records")
    parser.add_argument("review_sheet", type=Path, help="Output path visible to the reviewer")
    parser.add_argument("private_key", type=Path, help="Separate output path for the arm mapping")
    parser.add_argument("--arms", nargs="+", default=["B1_FIXED", "B2_AGENT_LOOP", "B3_VERIFIER_RECOVERY"])
    args = parser.parse_args()
    if args.review_sheet.resolve() == args.private_key.resolve():
        parser.error("review_sheet and private_key must use different paths")
    records = json.loads(args.input.read_text(encoding="utf-8"))
    sheet, key = build_blind_review(records, arms=tuple(args.arms))
    args.review_sheet.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.review_sheet.write_text(json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.private_key.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"review_responses": len(sheet["responses"]), "review_sheet": str(args.review_sheet), "private_key": str(args.private_key)}))


if __name__ == "__main__":
    main()
