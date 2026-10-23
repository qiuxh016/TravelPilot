"""Create v4 by aligning historical v3 fixtures to registered Advisor tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/evaluation/cases/advisor_multiturn_v3.json"
OUTPUT = ROOT / "tests/evaluation/cases/advisor_multiturn_v4.json"
REGISTERED_TOOLS = {
    "search_places", "get_trip_weather", "get_distance_matrix",
    "build_schedule_draft", "get_trip_route",
}
COMMIT_ACTIONS = {
    "COMMIT_CONFIRMED_DRAFT", "COMMIT_CONFIRMED_CANDIDATES",
    "SAVE_CONFIRMED_MEMORY", "UPDATE_CONFIRMED_MEMORY", "DELETE_CONFIRMED_MEMORY",
}
NO_TOOL_ACTIONS = COMMIT_ACTIONS | {"ASK_CONFIRMATION"}
EXPLAIN_ONLY_ACTIONS = {
    "EXPLAIN_UNSUPPORTED_BOOKING", "EXPLAIN_NO_EXTERNAL_BOOKING",
    "EXPLAIN_LINK_UNAVAILABLE", "REJECT_UNOWNED_PLACE",
}


def align_dataset(source: dict[str, Any]) -> dict[str, Any]:
    """Return a capability-aligned copy without changing the historical fixture."""
    dataset = json.loads(json.dumps(source))
    dataset["dataset_id"] = "advisor_multiturn_v4"
    dataset["version"] = 4
    dataset["scope"] = (
        "60 curated multi-turn travel tasks across ten families; allowed_tools is restricted "
        "to registered Advisor model tools. API/UI actions and user-confirmation commits are "
        "explicitly kept outside the model tool allowlist. Contract fixtures only, not a "
        "model-quality benchmark."
    )
    dataset["capability_contract"] = {
        "registered_advisor_tools": sorted(REGISTERED_TOOLS),
        "confirmation": "User confirmation and persistent writes are service/API state transitions, never model-callable tools.",
        "other_product_capabilities": "Content import and personal-memory APIs are separate product workflows, not currently registered in the Advisor tool loop.",
    }
    for case in dataset["cases"]:
        for turn in case["turns"]:
            prior = turn.get("allowed_tools", [])
            turn["allowed_tools"] = [name for name in prior if name in REGISTERED_TOOLS]
            removed = [name for name in prior if name not in REGISTERED_TOOLS]
            if removed:
                turn["non_agent_capabilities"] = removed
            action = turn["expected_action"]
            if action in NO_TOOL_ACTIONS:
                turn["allowed_tools"] = []
                turn["execution_boundary"] = (
                    "service_commit_after_explicit_user_confirmation"
                    if action in COMMIT_ACTIONS
                    else "ask_user; no model tool performs confirmation or commit"
                )
            elif action in EXPLAIN_ONLY_ACTIONS:
                turn["execution_boundary"] = "explain_or_reject_without_side_effect"
    dataset["scope_note"] = (
        "v3 is preserved as historical schema. v4 corrects tool capability semantics; "
        "this does not add missing tool implementations or score Agent behavior."
    )
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    result = align_dataset(source)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(result['cases'])} cases / {sum(len(c['turns']) for c in result['cases'])} turns")


if __name__ == "__main__":
    main()
