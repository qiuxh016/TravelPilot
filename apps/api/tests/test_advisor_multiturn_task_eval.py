import json
from copy import deepcopy
from pathlib import Path

from tests.evaluation.advisor_multiturn_task_eval import (
    _run_policy,
    evaluate_dataset,
)

DATASET = Path(__file__).parents[3] / "tests/evaluation/cases/advisor_multiturn_v5.json"


def test_scripted_multiturn_runner_scores_all_frozen_turns_without_live_services():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    report = evaluate_dataset(dataset)

    assert report["status"] == "SYNTHETIC_SCRIPTED_POLICY_REPLAY"
    assert report["provider_mode"] == "NO_LLM_NO_EXTERNAL_PROVIDERS"
    assert report["case_count"] == 60
    assert report["turn_count"] == 129
    assert report["groups"]["B1_FIXED_POLICY"]["action_matches"] == 51
    assert report["groups"]["B2_OBSERVATION_POLICY"]["action_matches"] == 86
    assert report["groups"]["B3_GUARDED_POLICY"]["policy_violations"] == []
    assert (
        report["groups"]["B2_OBSERVATION_POLICY"]["action_matches"]
        == report["groups"]["B3_GUARDED_POLICY"]["action_matches"]
    )
    assert (
        report["groups"]["B2_OBSERVATION_POLICY"]["owner_action_scores"]["AGENT_DECISION"]["turns"]
        == 76
    )
    assert "does not run the production Agent" in report["interpretation"]


def test_policy_decisions_are_independent_of_frozen_expected_answers():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(case for case in dataset["cases"] if case["id"] == "schedule-missing-dates-03")
    original = _run_policy(case, variant="B2_OBSERVATION_POLICY")
    mutated = deepcopy(case)
    for turn in mutated["turns"]:
        turn["expected_action"] = "IMPOSSIBLE_SCORING_SENTINEL"
        turn["expected_status"] = "FAILED"

    rerun = _run_policy(mutated, variant="B2_OBSERVATION_POLICY")

    assert [turn["action"] for turn in original["turns"]] == [
        turn["action"] for turn in rerun["turns"]
    ]
    assert [turn["status"] for turn in original["turns"]] == [
        turn["status"] for turn in rerun["turns"]
    ]


def test_guarded_policy_blocks_commit_after_confirmation_marker_is_removed():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    case = next(
        case for case in dataset["cases"]
        if case["id"] == "schedule-preserve-appointment-04"
    )
    case["turns"][-1]["confirmation_received"] = False

    unguarded = _run_policy(case, variant="B2_OBSERVATION_POLICY")
    guarded = _run_policy(case, variant="B3_GUARDED_POLICY")

    assert unguarded["turns"][-1]["action"] == "COMMIT_CONFIRMED_DRAFT"
    assert guarded["turns"][-1]["action"] == "SAFE_STOP"
    assert guarded["policy_violations"] == [
        "schedule-preserve-appointment-04:3:approval_gate"
    ]
