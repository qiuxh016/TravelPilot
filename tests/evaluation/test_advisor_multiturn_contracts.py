import copy
import json
from pathlib import Path

import pytest

from tests.evaluation.advisor_multiturn_contracts import validate_dataset
from tests.evaluation.align_multiturn_action_owners import align_dataset as align_ownership
from tests.evaluation.align_multiturn_capabilities import align_dataset

DATASET = Path(__file__).parent / "cases" / "advisor_multiturn_v1.json"
CAPABILITY_DATASET = Path(__file__).parent / "cases" / "advisor_multiturn_v4.json"
OWNERSHIP_DATASET = Path(__file__).parent / "cases" / "advisor_multiturn_v5.json"
V7_HOLDOUT = Path(__file__).parent / "cases" / "advisor_multiturn_holdout_v7.json"


def _dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_frozen_multi_turn_set_covers_at_least_eight_travel_families():
    report = validate_dataset(_dataset())

    assert report["evaluation_id"] == "advisor_multiturn_v1"
    assert report["status"] == "FROZEN_CONTRACT_SET_NOT_SCORED"
    assert report["case_count"] == 16
    assert report["turn_count"] >= 32
    assert len(report["family_counts"]) >= 8
    assert report["provider_mode"] == "synthetic_contract"
    assert "No Agent execution" in report["interpretation"]


def test_multiturn_contract_rejects_commit_without_explicit_confirmation():
    dataset = copy.deepcopy(_dataset())
    schedule = next(case for case in dataset["cases"] if case["id"] == "schedule-preserve-appointment-04")
    schedule["turns"][2]["confirmation_received"] = False

    with pytest.raises(ValueError, match="explicit user confirmation"):
        validate_dataset(dataset)


def test_multiturn_contract_rejects_duplicate_ids_and_single_turn_cases():
    dataset = copy.deepcopy(_dataset())
    dataset["cases"][1]["id"] = dataset["cases"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        validate_dataset(dataset)

    dataset = copy.deepcopy(_dataset())
    dataset["cases"][0]["turns"] = dataset["cases"][0]["turns"][:1]
    with pytest.raises(ValueError, match="2 to 6"):
        validate_dataset(dataset)


def test_v4_rejects_unregistered_tools_and_keeps_confirmation_outside_tool_loop():
    dataset = json.loads(CAPABILITY_DATASET.read_text(encoding="utf-8"))
    report = validate_dataset(dataset)
    assert report["case_count"] == 60
    assert report["turn_count"] == 129

    invalid = copy.deepcopy(dataset)
    invalid["cases"][0]["turns"][0]["allowed_tools"] = ["confirm_current_draft"]
    with pytest.raises(ValueError, match="unregistered Advisor tool"):
        validate_dataset(invalid)

    invalid = copy.deepcopy(dataset)
    commit = next(
        turn for case in invalid["cases"] for turn in case["turns"]
        if turn["expected_action"] == "COMMIT_CONFIRMED_DRAFT"
    )
    commit["allowed_tools"] = ["build_schedule_draft"]
    with pytest.raises(ValueError, match="confirmation/commit"):
        validate_dataset(invalid)


def test_v4_capability_alignment_is_reproducible_and_preserves_v3():
    source_path = Path(__file__).parent / "cases" / "advisor_multiturn_v3.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    frozen_copy = copy.deepcopy(source)
    assert align_dataset(source) == _capability_dataset()
    assert source == frozen_copy


def test_v5_assigns_every_turn_to_its_execution_owner_and_corrects_destination_workflow():
    source = json.loads(CAPABILITY_DATASET.read_text(encoding="utf-8"))
    frozen_copy = copy.deepcopy(source)
    expected = align_ownership(source)
    actual = json.loads(OWNERSHIP_DATASET.read_text(encoding="utf-8"))

    assert source == frozen_copy
    assert actual == expected
    assert actual["dataset_id"] == "advisor_multiturn_v5"
    assert len(actual["cases"]) == 60
    assert sum(len(case["turns"]) for case in actual["cases"]) == 129
    assert actual["ownership_counts"] == {
        "AGENT_DECISION": 76,
        "AGENT_RESPONSE": 8,
        "PRODUCT_WORKFLOW": 14,
        "USER_APPROVAL": 16,
        "RUNTIME_GUARD": 15,
    }
    assert validate_dataset(actual)["case_count"] == 60

    destination = next(
        turn for case in actual["cases"] for turn in case["turns"]
        if turn["expected_action"] == "RECOMMEND_DESTINATIONS"
    )
    assert destination["action_owner"] == "PRODUCT_WORKFLOW"
    assert destination["allowed_tools"] == []
    assert "recommend_destinations" in destination["non_agent_capabilities"]

    invalid = copy.deepcopy(actual)
    invalid_destination = next(
        turn for case in invalid["cases"] for turn in case["turns"]
        if turn["expected_action"] == "RECOMMEND_DESTINATIONS"
    )
    invalid_destination["allowed_tools"] = ["search_places"]
    with pytest.raises(ValueError, match="product workflow cannot be exposed"):
        validate_dataset(invalid)


def test_v7_holdout_has_adjudicated_action_sets_and_exact_reference_separation():
    from tests.evaluation.advisor_multiturn_contracts import (
        load_holdout_references,
        validate_holdout_separation,
    )

    dataset = json.loads(V7_HOLDOUT.read_text(encoding="utf-8"))
    report = validate_dataset(dataset)
    separation = validate_holdout_separation(
        dataset,
        load_holdout_references(dataset, cases_directory=V7_HOLDOUT.parent),
    )

    assert report["case_count"] == 20
    assert report["turn_count"] == 40
    assert separation["case_id_overlap_count"] == 0
    assert separation["exact_normalized_prompt_overlap_count"] == 0
    assert all(
        turn["expected_action"] in turn["acceptable_actions"]
        and turn["adjudication_note"]
        for case in dataset["cases"]
        for turn in case["turns"]
    )


def _capability_dataset():
    return json.loads(CAPABILITY_DATASET.read_text(encoding="utf-8"))
