import json
import subprocess
import sys
from copy import deepcopy

import pytest

from tests.evaluation.advisor_full_task_eval import ROOT, _run


def test_full_task_evaluator_cli_bootstraps_repository_import_path():
    script = ROOT / "tests/evaluation/advisor_full_task_eval.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "--dataset" in result.stdout
    assert "--output" in result.stdout


@pytest.mark.asyncio
async def test_frozen_advisor_full_task_contract_comparison_has_no_hard_invariant_violations():
    dataset = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v1.json"
    result = await _run(dataset)

    assert result["provider_mode"] == "DETERMINISTIC_PROVIDER_FIXTURES_AND_SCRIPTED_AGENT_POLICIES"
    assert result["source_provenance"]["git_revision"]
    assert len(result["source_provenance"]["source_sha256"][
        "tests/evaluation/advisor_full_task_eval.py"
    ]) == 64
    assert result["groups"]["B1_FIXED"]["case_count"] == 8
    assert result["groups"]["B2_AGENT_LOOP"]["case_count"] == 8
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["case_count"] == 8
    assert result["groups"]["B1_FIXED"]["hard_invariant_violations"] == 2
    assert result["groups"]["B2_AGENT_LOOP"]["hard_invariant_violations"] == 0
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["hard_invariant_violations"] == 0
    assert (
        result["groups"]["B2_AGENT_LOOP"]["expected_outcome_matches"]
        < result["groups"]["B3_VERIFIER_RECOVERY"]["expected_outcome_matches"]
    )


@pytest.mark.asyncio
async def test_expanded_advisor_contract_dataset_runs_all_32_matched_variants():
    dataset = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v2.json"
    result = await _run(dataset)

    assert result["evaluation_id"] == "advisor_existing_trip_agent_v2"
    assert all(group["case_count"] == 32 for group in result["groups"].values())
    assert result["groups"]["B1_FIXED"]["hard_invariant_violations"] == 8
    assert result["groups"]["B2_AGENT_LOOP"]["hard_invariant_violations"] == 0
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["hard_invariant_violations"] == 0
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["expected_action_matches"] == 32


@pytest.mark.asyncio
async def test_v3_covers_40_tasks_across_16_scenario_families_and_verifier_recovers():
    dataset = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v3.json"
    result = await _run(dataset)

    assert result["evaluation_id"] == "advisor_existing_trip_agent_v3"
    assert result["scenario_family_count"] == 16
    assert all(group["case_count"] == 40 for group in result["groups"].values())
    assert result["groups"]["B1_FIXED"]["expected_action_matches"] == 20
    assert result["groups"]["B1_FIXED"]["expected_outcome_matches"] == 29
    assert result["groups"]["B1_FIXED"]["hard_invariant_violations"] == 11
    assert result["groups"]["B2_AGENT_LOOP"]["expected_action_matches"] == 36
    assert result["groups"]["B2_AGENT_LOOP"]["expected_outcome_matches"] == 35
    assert result["groups"]["B2_AGENT_LOOP"]["hard_invariant_violations"] == 1
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["expected_action_matches"] == 40
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["expected_outcome_matches"] == 40
    assert result["groups"]["B3_VERIFIER_RECOVERY"]["hard_invariant_violations"] == 0


@pytest.mark.asyncio
async def test_scripted_loop_actions_do_not_read_case_or_family_labels():
    from tests.evaluation.advisor_full_task_eval import _run_agent, _run_fixed

    dataset = ROOT / "tests/evaluation/cases/advisor_existing_trip_agent_v3.json"
    cases = json.loads(dataset.read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["scenario_family"] == "malformed_route_matrix")
    relabeled = deepcopy(case)
    relabeled["id"] = "arbitrary-case-label"
    relabeled["scenario_family"] = "unrelated-family-label"

    original = await _run_agent("B3_VERIFIER_RECOVERY", case)
    changed = await _run_agent("B3_VERIFIER_RECOVERY", relabeled)

    assert changed == original
    assert _run_fixed(relabeled) == _run_fixed(case)
