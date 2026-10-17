from tests.evaluation.replacement_search_eval import run_evaluation


def test_replacement_search_evaluation_matches_all_recorded_cases():
    result = run_evaluation()

    assert result["evaluation_id"] == "weather_replacement_search_v1"
    assert result["scenario_count"] == 5
    assert result["all_expectations_match"]
    assert result["selected_count"] == 1
    assert result["rejected_count"] == 4
