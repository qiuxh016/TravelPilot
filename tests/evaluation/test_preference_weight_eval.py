from tests.evaluation.preference_weight_eval import run_evaluation


def test_default_weights_follow_explicit_preference_but_route_only_can_override_it():
    result = run_evaluation()

    assert result["scenario_count"] == 3
    assert result["all_expectations_match"] is True
    assert (
        result["label_source"] == "hand-authored synthetic preference ordering; not user study data"
    )
    by_id = {item["case_id"]: item for item in result["results"]}
    assert by_id["route_only"]["chosen_name"] == "fast low-interest option"
    assert by_id["default"]["chosen_name"] == "user-preferred option"
    assert by_id["preference_weight_2"]["chosen_name"] == "user-preferred option"
