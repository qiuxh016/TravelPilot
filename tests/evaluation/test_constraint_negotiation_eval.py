from tests.evaluation.constraint_negotiation_eval import run_evaluation


def test_constraint_negotiation_synthetic_comparison_covers_hard_cases():
    report = run_evaluation()

    assert report["suite"] == "constraint_negotiation_synthetic_v2"
    assert report["scenario_count"] == 10
    assert report["baselines"]["bounded_negotiator_solutions"] == 5
    assert report["baselines"]["single_change_solutions"] == 4
    assert report["hard_invariant_violations"] == 0
    assert report["input_rejections"] == [{
        "scenario": "outside_trip_date",
        "expected_rejection": "outside the trip dates",
        "rejected": True,
    }]

    fixed_flexible = next(
        item for item in report["cases"] if item["scenario"] == "fixed_and_flexible"
    )
    assert fixed_flexible["alternatives"] > 0
