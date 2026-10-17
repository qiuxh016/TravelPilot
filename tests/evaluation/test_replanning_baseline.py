from tests.evaluation.replanning_baseline import run_baseline


def test_current_replanning_baseline_is_reproducible_and_matches_recorded_behavior():
    result = run_baseline()

    assert result["baseline_id"] == "weather_aware_replan_v2"
    assert result["scenario_count"] == 7
    assert result["all_expectations_match"]
    assert result["total_changed_nodes"] == 6
    assert result["total_change_cost"] == 16
