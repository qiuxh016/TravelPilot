import math

import pytest

from tests.evaluation.preference_calibration import (
    DEFAULT_WEIGHTS,
    calibrate,
    run_synthetic_demo,
)


def _candidate(candidate_id, *, route, preference):
    return {
        "candidate_id": candidate_id,
        "features": {
            "plan_change_cost": 6,
            "route_time_delta_min": route,
            "time_shift_min": 0,
            "preference_penalty": preference,
            "unfilled_visit_penalty": 0,
        },
    }


def _dataset(trip_count=6):
    return {
        "schema_version": 1,
        "comparisons": [
            {
                "trip_id": f"trip-{trip_index}",
                "selected_candidate_id": "preferred",
                "candidates": [
                    _candidate("preferred", route=30, preference=0),
                    _candidate("fast", route=0, preference=3),
                ],
            }
            for trip_index in range(trip_count)
            for _ in range(2)
        ],
    }


def test_calibration_uses_trip_grouped_validation_and_keeps_app_defaults_unchanged():
    report = calibrate(_dataset())

    assert report["status"] == "CALIBRATED"
    assert report["default_grouped_cv_accuracy"] == 0.5
    assert report["calibrated_grouped_cv_accuracy"] == 1.0
    assert report["recommended_weights"]["route_time_delta_min"] == 0.05
    assert report["application_defaults_changed"] is False
    assert DEFAULT_WEIGHTS["preference_penalty"] == 1.0


def test_calibration_refuses_to_fit_below_minimum_trip_and_session_counts():
    report = calibrate(_dataset(trip_count=4))

    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["recommended_weights"] == DEFAULT_WEIGHTS
    assert report["default_grouped_cv_accuracy"] is None


def test_calibration_keeps_defaults_when_feedback_does_not_improve_grouped_validation():
    dataset = _dataset()
    for comparison in dataset["comparisons"]:
        comparison["selected_candidate_id"] = "fast"
        comparison["candidates"] = [
            _candidate("fast", route=0, preference=0),
            _candidate("preferred", route=30, preference=0),
        ]

    report = calibrate(dataset)

    assert report["status"] == "KEEP_DEFAULTS"
    assert report["recommended_weights"] == DEFAULT_WEIGHTS
    assert report["application_defaults_changed"] is False


@pytest.mark.parametrize("invalid", [True, -1, math.inf, math.nan, "3"])
def test_calibration_rejects_invalid_feature_values(invalid):
    dataset = _dataset()
    dataset["comparisons"][0]["candidates"][0]["features"]["route_time_delta_min"] = invalid

    with pytest.raises(ValueError, match="non-negative numbers"):
        calibrate(dataset)


def test_calibration_rejects_a_choice_not_present_in_candidates():
    dataset = _dataset()
    dataset["comparisons"][0]["selected_candidate_id"] = "unshown"

    with pytest.raises(ValueError, match="selected candidate is absent"):
        calibrate(dataset)


def test_synthetic_demo_exercises_calibration_without_claiming_empirical_effect():
    report = run_synthetic_demo()

    assert report["status"] == "CALIBRATED"
    assert report["input_source"] == "SYNTHETIC_DETERMINISTIC"
    assert report["default_grouped_cv_accuracy"] == 0.5
    assert report["calibrated_grouped_cv_accuracy"] == 1.0
    assert "not empirical" in report["scope"]
