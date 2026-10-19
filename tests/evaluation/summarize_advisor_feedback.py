"""Summarize Advisor reply ratings without printing conversation content."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

RATINGS = {"HELPFUL", "NOT_HELPFUL"}
REASONS = {
    "MISSED_REQUEST", "INACCURATE", "NOT_ACTIONABLE", "TOO_VERBOSE", "OTHER",
}


def summarize_feedback(document: dict[str, Any]) -> dict[str, Any]:
    data = document.get("data")
    if isinstance(data, dict) and isinstance(data.get("advisor_messages"), list):
        messages = data["advisor_messages"]
    elif isinstance(document.get("messages"), list):
        messages = document["messages"]
    else:
        raise TypeError("input must be an account export or Advisor session with messages")

    assistant_replies = 0
    rated_replies = 0
    helpful = 0
    reasons: Counter[str] = Counter()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        assistant_replies += 1
        payload = message.get("payload")
        feedback = payload.get("advisor_feedback") if isinstance(payload, dict) else None
        if feedback is None:
            continue
        if not isinstance(feedback, dict) or feedback.get("rating") not in RATINGS:
            raise ValueError("assistant message contains invalid advisor_feedback")
        rating = feedback["rating"]
        reason = feedback.get("reason")
        if reason is not None and reason not in REASONS:
            raise ValueError("assistant message contains an invalid feedback reason")
        if rating == "HELPFUL" and reason is not None:
            raise ValueError("helpful feedback cannot have a negative reason")
        rated_replies += 1
        if rating == "HELPFUL":
            helpful += 1
        elif reason:
            reasons[reason] += 1

    return {
        "report_type": "advisor_user_feedback_summary_v1",
        "assistant_reply_count": assistant_replies,
        "rated_reply_count": rated_replies,
        "unrated_reply_count": assistant_replies - rated_replies,
        "helpful_count": helpful,
        "not_helpful_count": rated_replies - helpful,
        "helpful_rate_percent": round(100 * helpful / rated_replies, 1) if rated_replies else None,
        "not_helpful_reasons": dict(sorted(reasons.items())),
        "limitations": [
            "Self-selected ratings are descriptive and may be affected by response bias.",
            "These counts do not establish causal improvement or representativeness.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="account export JSON or Advisor session JSON")
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        parser.error("input JSON root must be an object")
    print(json.dumps(summarize_feedback(document), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
