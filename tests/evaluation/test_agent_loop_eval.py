from tests.evaluation.agent_loop_eval import run_evaluation


def test_agent_loop_acceptance_scenarios_have_expected_states_and_counts():
    report = run_evaluation()

    assert report["scenario_count"] == 4
    assert report["all_expectations_match"] is True
    assert [item["status"] for item in report["results"]] == [
        "SUCCEEDED", "REJECTED", "FAILED", "STEP_LIMIT"
    ]
