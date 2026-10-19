"""Validate and summarize a completed single-system Advisor quality review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCORE_FIELDS = (
    "factual_grounding",
    "constraint_safety",
    "task_progress",
    "clarification_quality",
    "transparency",
)


def summarize_review(sheet: dict[str, Any]) -> dict[str, Any]:
    if sheet.get("review_protocol") != "advisor_single_system_quality_review_v1":
        raise ValueError("unsupported product quality review protocol")
    rows = sheet.get("responses")
    if not isinstance(rows, list) or not rows:
        raise ValueError("review sheet must contain responses")

    ids: set[str] = set()
    scores_by_field: dict[str, list[int]] = {field: [] for field in SCORE_FIELDS}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("each review response must be an object")
        review_id = row.get("review_id")
        if not isinstance(review_id, str) or not review_id or review_id in ids:
            raise ValueError("review IDs must be unique non-empty strings")
        ids.add(review_id)
        conversation = row.get("conversation")
        if not isinstance(conversation, list) or not conversation:
            raise ValueError(f"{review_id} has no conversation")
        for turn in conversation:
            if not isinstance(turn, dict) or any(
                not isinstance(turn.get(field), str) or not turn[field].strip()
                for field in ("user_message", "advisor_reply")
            ):
                raise ValueError(f"{review_id} has an incomplete conversation turn")
        scores = row.get("scores")
        if not isinstance(scores, dict):
            raise TypeError(f"{review_id} has no score object")
        for field in SCORE_FIELDS:
            value = scores.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(f"{review_id}.{field} must be an integer from 1 to 5")
            scores_by_field[field].append(value)
        if not isinstance(scores.get("reviewer_notes", ""), str):
            raise TypeError(f"{review_id}.reviewer_notes must be text")

    means = {
        field: round(sum(values) / len(values), 3)
        for field, values in scores_by_field.items()
    }
    return {
        "review_protocol": "advisor_single_system_quality_summary_v1",
        "assessment_type": "descriptive human ratings for one system; no comparison or significance claim",
        "reviewed_conversations": len(rows),
        "reviewed_turns": sum(len(row["conversation"]) for row in rows),
        "mean_score_by_dimension": means,
        "overall_mean": round(sum(means.values()) / len(means), 3),
        "interpretation": "Descriptive reviewer scores only. These ratings do not establish comparative improvement or statistical significance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_sheet", type=Path)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    if args.review_sheet.resolve() == args.summary.resolve():
        parser.error("summary output must not overwrite the review sheet")
    if args.summary.exists():
        parser.error("refusing to overwrite an existing summary")
    sheet = json.loads(args.review_sheet.read_text(encoding="utf-8"))
    summary = summarize_review(sheet)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed_conversations": summary["reviewed_conversations"], "summary": str(args.summary)}))


if __name__ == "__main__":
    main()
