"""Build the v5 ownership-aligned copy from frozen Advisor contract v4."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tests.evaluation.advisor_action_ownership import action_owner, owner_counts

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/evaluation/cases/advisor_multiturn_v4.json"
OUTPUT = ROOT / "tests/evaluation/cases/advisor_multiturn_v5.json"


def align_dataset(source: dict[str, Any]) -> dict[str, Any]:
    """Return a new contract version; never mutate historical v4 fixtures."""
    dataset = json.loads(json.dumps(source))
    source_hash = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    dataset["dataset_id"] = "advisor_multiturn_v5"
    dataset["version"] = 5
    dataset["status"] = "CAPABILITY_OWNERSHIP_ALIGNED_NOT_SCORED"
    dataset["scope"] = (
        "60 frozen multi-turn contracts / 129 user turns across ten travel families. "
        "Every expected outcome identifies whether it belongs to an Agent decision, "
        "assistant response, product workflow, user approval or runtime guard. "
        "This ownership contract is not Agent execution or a quality benchmark."
    )
    for case in dataset["cases"]:
        for turn in case["turns"]:
            # Destination comparison is currently a dedicated API workflow that
            # calls the map provider directly; it is not a registered model tool.
            if turn["expected_action"] == "RECOMMEND_DESTINATIONS":
                turn["allowed_tools"] = []
                capabilities = set(turn.get("non_agent_capabilities", []))
                capabilities.add("recommend_destinations")
                turn["non_agent_capabilities"] = sorted(capabilities)
                turn["execution_boundary"] = (
                    "dedicated_product_endpoint_runs_bounded_map_searches_without_model_tool_selection"
                )
            turn["action_owner"] = action_owner(turn)
    dataset["capability_contract"] = {
        "registered_advisor_tools": sorted({
            tool for case in dataset["cases"] for turn in case["turns"]
            for tool in turn["allowed_tools"]
        }),
        "action_owners": {
            "AGENT_DECISION": "The constrained Agent chooses a registered tool or asks/continues within the supplied scope.",
            "AGENT_RESPONSE": "The Advisor produces a user-facing response; no model tool call is expected for this turn.",
            "PRODUCT_WORKFLOW": "A dedicated API/UI/domain service performs the outcome outside the model tool loop.",
            "USER_APPROVAL": "The user must approve; any persistent write is performed by the service after explicit approval.",
            "RUNTIME_GUARD": "A deterministic safety, lifecycle, capability or recovery rule controls the outcome.",
        },
        "confirmation": "Confirmation prompts and commits are separate service/API transitions, never model-callable tools.",
        "source_dataset": "advisor_multiturn_v4",
        "source_dataset_sha256": source_hash,
    }
    dataset["scope_note"] = (
        "v4 remains preserved as the tool-allowlist-aligned historical contract. v5 adds "
        "outcome ownership and corrects destination recommendation: the current dedicated "
        "endpoint performs bounded map searches directly rather than exposing search_places "
        "to the model loop. No Agent execution or quality score is implied."
    )
    dataset["ownership_counts"] = owner_counts(dataset)
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    result = align_dataset(source)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "dataset_id": result["dataset_id"],
        "cases": len(result["cases"]),
        "turns": sum(len(case["turns"]) for case in result["cases"]),
        "ownership_counts": result["ownership_counts"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
