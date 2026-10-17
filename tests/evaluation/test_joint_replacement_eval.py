from tests.evaluation.joint_replacement_eval import run_evaluation


def test_joint_assignment_improves_route_cost_without_increasing_plan_change_cost():
    result = run_evaluation()

    assert result["scenario_count"] == 3
    assert result["all_expectations_match"] is True
    case = result["results"][0]
    assert case["greedy_assignment"] == ["b", "a"]
    assert case["joint_assignment"] == ["a", "b"]
    assert case["greedy_route_cost_min"] == 30
    assert case["joint_route_cost_min"] == 2
    assert case["narrow_beam_assignment"] == ["b", "a"]
    assert case["narrow_beam_route_cost_min"] == 30
    assert case["joint_objective"]["weighted_score"] == 16.2
    assert case["joint_objective_weights"] == {
        "plan_change_cost": 1.0,
        "route_time_delta_min": 0.1,
        "time_shift_min": 0.05,
        "preference_penalty": 1.0,
        "unfilled_visit_penalty": 1.0,
    }
    assert case["joint_plan_change_cost"] == case["greedy_plan_change_cost"] == 12
    multi_day = result["results"][1]
    assert multi_day["joint_assignment"] == ["only-option", None]
    long_detour = result["results"][2]
    assert long_detour["joint_assignment"] == [None]
    assert long_detour["joint_reasons"] == ["OBJECTIVE_TRADEOFF"]
    assert long_detour["joint_plan_change_cost"] == 3
    assert long_detour["greedy_plan_change_cost"] == 6
