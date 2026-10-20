import json
from pathlib import Path

import pytest

from tests.evaluation.advisor_multiturn_protocol_eval import evaluate_dataset

DATASET = Path(__file__).parents[3] / "tests/evaluation/cases/advisor_multiturn_v4.json"
LATEST_DATASET = Path(__file__).parents[3] / "tests/evaluation/cases/advisor_multiturn_v5.json"


def test_frozen_multi_turn_cases_replay_through_production_state_helpers():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    report = evaluate_dataset(dataset)

    assert report["passed"] is True
    assert report["evaluation_id"] == "advisor_multiturn_v4_protocol"
    assert report["dataset_id"] == "advisor_multiturn_v4"
    assert report["case_count"] == 60
    assert report["turn_count"] >= 120
    assert report["resumed_turns"] > 0
    assert report["continuity_checks"] == report["resumed_turns"]
    assert report["continuity_failures"] == []
    assert report["approval_gate_cases"] == 7
    assert report["approval_gate_failures"] == []
    assert report["stale_context_checks"] == 1
    assert report["stale_context_failures"] == []
    assert report["cancellation_checks"] == 1
    assert report["cancellation_failures"] == []
    assert "No Agent/model/tool execution" in report["interpretation"]


def test_protocol_replay_detects_missing_confirmation():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if any(
        turn["expected_action"] == "COMMIT_CONFIRMED_DRAFT" for turn in case["turns"]
    ))
    commit = next(turn for turn in case["turns"] if turn["expected_action"] == "COMMIT_CONFIRMED_DRAFT")
    commit["confirmation_received"] = False

    with pytest.raises(ValueError, match="explicit user confirmation"):
        evaluate_dataset(dataset)


def test_protocol_replay_detects_stale_context_answer_that_would_be_accepted():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["id"] == "recovery-stale-context-17")
    case["turns"][1]["context_revision_after"] = 7

    report = evaluate_dataset(dataset)

    assert report["passed"] is False
    assert report["stale_context_failures"] == ["recovery-stale-context-17:2"]


def test_protocol_replay_detects_cancellation_expected_for_non_cancel_answer():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["id"] == "recovery-cancel-pending-18")
    case["turns"][1]["user"] = "继续安排"

    report = evaluate_dataset(dataset)

    assert report["passed"] is False
    assert report["cancellation_failures"] == ["recovery-cancel-pending-18:2"]


def test_latest_v5_dataset_replays_action_ownership_and_protocol_contracts():
    dataset = json.loads(LATEST_DATASET.read_text(encoding="utf-8"))

    report = evaluate_dataset(dataset)

    assert report["passed"] is True
    assert report["evaluation_id"] == "advisor_multiturn_v5_protocol"
    assert report["dataset_id"] == "advisor_multiturn_v5"
    assert report["case_count"] == 60
    assert report["turn_count"] == 129
    assert report["approval_gate_cases"] == 7
    assert report["continuity_failures"] == []
    assert report["approval_gate_failures"] == []
