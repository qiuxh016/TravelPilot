import asyncio

from tests.evaluation.advisor_verifier_recovery_ablation import ARMS, evaluate


def test_production_loop_verifier_prevents_draft_from_malformed_route_matrix():
    report = asyncio.run(evaluate())
    summary = report["summary"]

    assert summary["scenario_count"] == 3
    assert summary["valid_control_draft_calls"] == {arm: 1 for arm in ARMS}
    assert summary["malformed_matrix_draft_calls"][ARMS[0]] == 1
    assert summary["malformed_matrix_draft_calls"][ARMS[1]] == 0
    assert summary["malformed_matrix_accepted_by_b2"] is True
    assert summary["malformed_matrix_rejected_by_b3"] is True


def test_production_loop_recovery_stops_after_provider_failure_without_retrying():
    report = asyncio.run(evaluate())
    summary = report["summary"]

    assert summary["provider_outage_b2_terminal"] == "FAILED"
    assert summary["provider_outage_b3_terminal"] == "SUCCEEDED"
    assert summary["provider_outage_task_outcomes"] == {
        ARMS[0]: "FAILED",
        ARMS[1]: "SAFE_STOP_UNVERIFIED",
    }
    assert summary["provider_outage_b3_repeated_failed_call"] is False
