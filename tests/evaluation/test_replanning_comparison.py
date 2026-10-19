from tests.evaluation.replanning_comparison import run_comparison


def test_evidence_aware_weather_rules_avoid_category_only_false_removals():
    report = run_comparison()
    assert report["comparison_id"] == "weather_replanning_category_vs_evidence_v2"
    assert report["scenario_count"] == 3
    assert report["avoided_unverified_removal_count"] == 3
    weather_case = next(item for item in report["results"] if item["case_id"] == "weather-removes-only-verified-sensitive-outdoor")
    assert weather_case["legacy_category_heuristic_removed"] == ["museum", "park"]
    assert weather_case["evidence_aware_removed"] == ["park"]
    low_confidence = next(
        item for item in report["results"]
        if item["case_id"] == "weather-low-confidence-profile-kept"
    )
    assert low_confidence["legacy_category_heuristic_removed"] == [
        "inferred-high-sensitivity",
    ]
    assert low_confidence["evidence_aware_removed"] == []
