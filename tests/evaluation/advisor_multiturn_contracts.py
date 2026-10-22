"""Validate the frozen multi-turn Advisor contract set; this does not score an Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from tests.evaluation.advisor_action_ownership import action_owner
from tests.evaluation.provenance import evaluation_provenance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evaluation/cases/advisor_multiturn_v1.json"
RESULT = ROOT / "tests/evaluation/results/advisor_multiturn_v1_validation.json"
TASK_TYPES = {
    "DISCOVERY",
    "SCHEDULE",
    "SEARCH",
    "MEAL",
    "LODGING",
    "TRANSPORT",
    "WEATHER",
    "PREPARATION",
    "MEMORY",
    "GENERAL",
}
STATUSES = {
    "WAITING_USER",
    "PROPOSED",
    "COMPLETED",
    "PARTIAL",
    "CANCELLED",
    "STALE",
    "FAILED",
}
CONFIRMATION_ACTIONS = {
    "COMMIT_CONFIRMED_DRAFT",
    "COMMIT_CONFIRMED_CANDIDATES",
    "SAVE_CONFIRMED_MEMORY",
    "UPDATE_CONFIRMED_MEMORY",
    "DELETE_CONFIRMED_MEMORY",
}
REGISTERED_ADVISOR_TOOLS = {
    "search_places",
    "get_trip_weather",
    "get_distance_matrix",
    "build_schedule_draft",
    "get_trip_route",
}
EXPECTED_AGENT_TOOLS = {
    "SEARCH_PLACES": "search_places",
    "REFINE_CANDIDATES": "search_places",
    "GET_TRIP_WEATHER": "get_trip_weather",
    "GET_DISTANCE_MATRIX": "get_distance_matrix",
    "BUILD_SCHEDULE_DRAFT": "build_schedule_draft",
    "GET_TRIP_ROUTE": "get_trip_route",
}


def _is_holdout_dataset(dataset_id: Any) -> bool:
    return isinstance(dataset_id, str) and dataset_id.startswith("advisor_multiturn_holdout_v")


def validate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed or unsafe-to-score fixtures before a runner is added."""
    if dataset.get("provider_mode") != "synthetic_contract":
        raise ValueError("multi-turn contract fixtures must remain separate from live results")
    if dataset.get("frozen_before_scoring") is not True:
        raise ValueError("dataset must be frozen before scoring")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset requires a nonempty cases array")

    ids: set[str] = set()
    families: Counter[str] = Counter()
    turn_count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise TypeError("each case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError("case ids must be nonempty and unique")
        ids.add(case_id)
        if case.get("task_type") not in TASK_TYPES:
            raise ValueError(f"{case_id}: unsupported task type")
        family = case.get("family")
        if not isinstance(family, str) or not family:
            raise ValueError(f"{case_id}: missing family")
        families[family] += 1
        if not isinstance(case.get("initial_state"), dict):
            raise TypeError(f"{case_id}: initial_state must be an object")
        invariants = case.get("invariants")
        if (
            not isinstance(invariants, list)
            or not invariants
            or any(not isinstance(item, str) or not item for item in invariants)
        ):
            raise ValueError(f"{case_id}: safety/quality invariants are required")
        turns = case.get("turns")
        if not isinstance(turns, list) or not 2 <= len(turns) <= 6:
            raise ValueError(f"{case_id}: expected 2 to 6 user turns")
        turn_count += len(turns)
        for index, turn in enumerate(turns, start=1):
            if (
                not isinstance(turn, dict)
                or not isinstance(turn.get("user"), str)
                or not turn["user"].strip()
            ):
                raise ValueError(f"{case_id} turn {index}: user input is required")
            if not isinstance(turn.get("expected_action"), str) or not turn["expected_action"]:
                raise ValueError(f"{case_id} turn {index}: expected_action is required")
            if turn.get("expected_status") not in STATUSES:
                raise ValueError(f"{case_id} turn {index}: unsupported expected_status")
            tools = turn.get("allowed_tools")
            if not isinstance(tools, list) or any(
                not isinstance(tool, str) or not tool for tool in tools
            ):
                raise ValueError(f"{case_id} turn {index}: allowed_tools must be a string array")
            if _is_holdout_dataset(dataset.get("dataset_id")):
                expected_tool = EXPECTED_AGENT_TOOLS.get(turn["expected_action"])
                if expected_tool and expected_tool not in tools:
                    raise ValueError(
                        f"{case_id} turn {index}: expected tool is absent from the allowlist"
                    )
            if dataset.get("dataset_id") in {"advisor_multiturn_v4", "advisor_multiturn_v5"} or _is_holdout_dataset(dataset.get("dataset_id")):
                unknown_tools = set(tools) - REGISTERED_ADVISOR_TOOLS
                if unknown_tools:
                    raise ValueError(
                        f"{case_id} turn {index}: unregistered Advisor tool(s): "
                        f"{', '.join(sorted(unknown_tools))}"
                    )
                if (
                    turn.get("expected_action") in CONFIRMATION_ACTIONS | {"ASK_CONFIRMATION"}
                    and tools
                ):
                    raise ValueError(
                        f"{case_id} turn {index}: confirmation/commit must not be model-callable"
                    )
                non_agent = turn.get("non_agent_capabilities", [])
                if not isinstance(non_agent, list) or any(
                    not isinstance(name, str) or name in REGISTERED_ADVISOR_TOOLS
                    for name in non_agent
                ):
                    raise ValueError(
                        f"{case_id} turn {index}: invalid non-agent capability annotation"
                    )
            if dataset.get("dataset_id") == "advisor_multiturn_v5" or _is_holdout_dataset(dataset.get("dataset_id")):
                expected_owner = action_owner(turn)
                if turn.get("action_owner") != expected_owner:
                    raise ValueError(
                        f"{case_id} turn {index}: action_owner must be {expected_owner}"
                    )
                if expected_owner == "PRODUCT_WORKFLOW" and tools:
                    raise ValueError(
                        f"{case_id} turn {index}: product workflow cannot be exposed as a model tool"
                    )
                if expected_owner == "USER_APPROVAL" and tools:
                    raise ValueError(
                        f"{case_id} turn {index}: user approval cannot be model-callable"
                    )
            if turn.get("expected_action") in CONFIRMATION_ACTIONS:
                if turn.get("confirmation_received") is not True or index == 1:
                    raise ValueError(
                        f"{case_id} turn {index}: commit requires explicit user confirmation"
                    )
                if turns[index - 2].get("expected_status") != "WAITING_USER":
                    raise ValueError(
                        f"{case_id} turn {index}: commit requires a preceding approval wait"
                    )
    if _is_holdout_dataset(dataset.get("dataset_id")):
        protocol = dataset.get("holdout_protocol")
        if not isinstance(protocol, dict):
            raise ValueError("holdout protocol metadata is required")
        references = protocol.get("reference_datasets")
        reference_hashes = protocol.get("reference_dataset_sha256")
        defaults = protocol.get("default_case_ids")
        if (
            not isinstance(references, list)
            or not references
            or len(set(references)) != len(references)
            or not isinstance(reference_hashes, dict)
            or set(reference_hashes) != set(references)
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in reference_hashes.values()
            )
            or not isinstance(defaults, list)
            or not defaults
            or len(set(defaults)) != len(defaults)
            or not set(defaults) <= ids
        ):
            raise ValueError("holdout reference datasets and default case selection must be valid")
        # EXPOSED datasets remain schema-valid for audit and future overlap
        # checks, but live evaluation runners must refuse to score them again.
        allowed_statuses = {"FROZEN_NOT_SCORED", "EXPOSED"}
        if dataset.get("dataset_id") == "advisor_multiturn_holdout_v5":
            # Scripted baselines may be replayed locally before the first live
            # model score; that result must remain explicitly non-live.
            allowed_statuses.add("FROZEN_NOT_LIVE_SCORED")
        if dataset.get("status") not in allowed_statuses:
            raise ValueError("holdout must remain frozen until its first live scoring run")

    digest = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "evaluation_id": dataset.get("dataset_id"),
        "status": dataset.get("status"),
        "provider_mode": dataset["provider_mode"],
        "case_count": len(cases),
        "turn_count": turn_count,
        "family_counts": dict(sorted(families.items())),
        "case_file_sha256": digest,
        "source_provenance": evaluation_provenance(
            "advisor_multiturn_contracts_holdout"
            if _is_holdout_dataset(dataset.get("dataset_id"))
            else "advisor_multiturn_contracts_v5"
            if dataset.get("dataset_id") == "advisor_multiturn_v5"
            else "advisor_multiturn_contracts_v4"
            if dataset.get("dataset_id") == "advisor_multiturn_v4"
            else "advisor_multiturn_contracts_v3"
            if dataset.get("dataset_id") == "advisor_multiturn_v3"
            else "advisor_multiturn_contracts_v2"
            if dataset.get("dataset_id") == "advisor_multiturn_v2"
            else "advisor_multiturn_contracts"
        ),
        "interpretation": "Schema/coverage validation only. No Agent execution, quality score, or live provider result.",
    }


def validate_holdout_separation(
    dataset: dict[str, Any],
    reference_datasets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify a reserved set has no exact case-ID or prompt overlap with references."""
    protocol = dataset.get("holdout_protocol")
    if not _is_holdout_dataset(dataset.get("dataset_id")) or not isinstance(protocol, dict):
        raise ValueError("a declared advisor multi-turn holdout dataset is required")
    expected_refs = protocol.get("reference_datasets")
    if not isinstance(expected_refs, list) or set(expected_refs) != set(reference_datasets):
        raise ValueError("all declared reference datasets must be supplied exactly once")

    def normalized_prompts(source: dict[str, Any]) -> set[str]:
        return {
            " ".join(unicodedata.normalize("NFKC", turn["user"]).split()).casefold()
            for case in source.get("cases", [])
            for turn in case.get("turns", [])
            if isinstance(turn, dict) and isinstance(turn.get("user"), str)
        }

    holdout_ids = {case.get("id") for case in dataset.get("cases", [])}
    holdout_prompts = normalized_prompts(dataset)
    overlaps: dict[str, dict[str, list[str]]] = {}
    reference_counts: dict[str, int] = {}
    for reference_id, reference in reference_datasets.items():
        if reference.get("dataset_id") != reference_id:
            raise ValueError(f"reference dataset identity mismatch: {reference_id}")
        ref_ids = {case.get("id") for case in reference.get("cases", [])}
        ref_prompts = normalized_prompts(reference)
        duplicate_ids = sorted(holdout_ids & ref_ids)
        duplicate_prompts = sorted(holdout_prompts & ref_prompts)
        if duplicate_ids or duplicate_prompts:
            overlaps[reference_id] = {
                "case_ids": duplicate_ids,
                "normalized_prompts": duplicate_prompts,
            }
        reference_counts[reference_id] = len(ref_ids)
    if overlaps:
        raise ValueError(f"holdout overlaps declared references: {overlaps}")
    reference_hashes = {
        reference_id: hashlib.sha256(
            json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        for reference_id, reference in sorted(reference_datasets.items())
    }
    if protocol.get("reference_dataset_sha256") != reference_hashes:
        raise ValueError("a frozen reference dataset changed after the holdout was registered")
    return {
        "holdout_dataset_id": dataset["dataset_id"],
        "holdout_case_count": len(holdout_ids),
        "holdout_prompt_count": len(holdout_prompts),
        "reference_case_counts": reference_counts,
        "case_id_overlap_count": 0,
        "exact_normalized_prompt_overlap_count": 0,
        "interpretation": "Exact ID and normalized prompt disjointness only; semantic independence is not established.",
        "reference_dataset_sha256": reference_hashes,
    }


def load_holdout_references(
    dataset: dict[str, Any],
    *,
    cases_directory: Path,
) -> dict[str, dict[str, Any]]:
    """Load every declared frozen reference set, failing closed if one is absent."""
    protocol = dataset.get("holdout_protocol")
    references = protocol.get("reference_datasets") if isinstance(protocol, dict) else None
    if not isinstance(references, list) or not references:
        raise ValueError("holdout reference dataset declarations are required")
    loaded = {}
    for dataset_id in references:
        path = cases_directory / f"{dataset_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"declared holdout reference is missing: {dataset_id}")
        loaded[dataset_id] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=RESULT)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = validate_dataset(dataset)
    if _is_holdout_dataset(dataset.get("dataset_id")):
        references = load_holdout_references(dataset, cases_directory=args.dataset.parent)
        report["holdout_separation"] = validate_holdout_separation(dataset, references)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "source_provenance"},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
