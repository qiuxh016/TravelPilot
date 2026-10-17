from tests.evaluation.user_add_eval import run_evaluation


def test_user_add_scenarios_match_feasibility_and_preserve_fixed_nodes():
    result = run_evaluation()

    assert result["case_count"] == 7
    assert result["all_expectations_match"] is True
    assert all(case["expected_match"] for case in result["results"])
