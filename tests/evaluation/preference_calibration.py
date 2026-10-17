"""Offline, trip-grouped calibration from explicit pairwise user choices.

Input is an opt-in JSON export of comparisons. The tool never calls providers,
persists user data, or changes application defaults; it emits aggregate metrics
and a suggested set of route/time/preference weights only.
"""

from __future__ import annotations

import argparse
import itertools
import json
from math import isfinite
from pathlib import Path
from typing import Any

DEFAULT_WEIGHTS = {
    "plan_change_cost": 1.0,
    "route_time_delta_min": 0.1,
    "time_shift_min": 0.05,
    "preference_penalty": 1.0,
    "unfilled_visit_penalty": 1.0,
}
MIN_TRIPS = 5
MIN_COMPARISONS = 10
_GRID = (
    ("route_time_delta_min", (0.0, 0.05, 0.1, 0.2, 0.5)),
    ("time_shift_min", (0.0, 0.025, 0.05, 0.1, 0.2)),
    ("preference_penalty", (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)),
)
_FEATURES = tuple(DEFAULT_WEIGHTS)


def _validate_dataset(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("expected schema_version=1")
    comparisons = payload.get("comparisons")
    if not isinstance(comparisons, list):
        raise TypeError("comparisons must be a list")
    normalized = []
    for index, comparison in enumerate(comparisons):
        if not isinstance(comparison, dict):
            raise TypeError(f"comparison {index} must be an object")
        trip_id = comparison.get("trip_id")
        selected_id = comparison.get("selected_candidate_id")
        candidates = comparison.get("candidates")
        if not isinstance(trip_id, str) or not trip_id.strip():
            raise ValueError(f"comparison {index} requires trip_id")
        if not isinstance(selected_id, str) or not selected_id:
            raise ValueError(f"comparison {index} requires selected_candidate_id")
        if not isinstance(candidates, list) or len(candidates) < 2:
            raise ValueError(f"comparison {index} requires at least two candidates")
        features_by_id: dict[str, dict[str, float]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise TypeError(f"comparison {index} candidate must be an object")
            candidate_id = candidate.get("candidate_id")
            features = candidate.get("features")
            if not isinstance(candidate_id, str) or not candidate_id or candidate_id in features_by_id:
                raise ValueError(f"comparison {index} candidate IDs must be unique strings")
            if not isinstance(features, dict) or set(features) != set(_FEATURES):
                raise ValueError(f"comparison {index} candidate features do not match schema")
            parsed: dict[str, float] = {}
            for key, value in features.items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not isfinite(value)
                    or value < 0
                ):
                    raise ValueError(f"comparison {index} feature values must be non-negative numbers")
                parsed[key] = float(value)
            features_by_id[candidate_id] = parsed
        if selected_id not in features_by_id:
            raise ValueError(f"comparison {index} selected candidate is absent")
        normalized.append({"trip_id": trip_id, "selected_id": selected_id, "features": features_by_id})
    return normalized


def _cost(features: dict[str, float], weights: dict[str, float]) -> float:
    return sum(features[key] * weights[key] for key in _FEATURES)


def _pairwise_accuracy(
    comparisons: list[dict[str, Any]], weights: dict[str, float]
) -> tuple[float | None, int]:
    correct = 0.0
    total = 0
    for comparison in comparisons:
        selected = comparison["features"][comparison["selected_id"]]
        selected_cost = _cost(selected, weights)
        for candidate_id, features in comparison["features"].items():
            if candidate_id == comparison["selected_id"]:
                continue
            other_cost = _cost(features, weights)
            correct += 1.0 if selected_cost < other_cost else 0.5 if selected_cost == other_cost else 0.0
            total += 1
    return (correct / total if total else None), total


def _fit_weights(comparisons: list[dict[str, Any]]) -> dict[str, float]:
    best = DEFAULT_WEIGHTS.copy()
    best_score = -1.0
    best_distance = float("inf")
    dimensions = [values for _, values in _GRID]
    for values in itertools.product(*dimensions):
        candidate = DEFAULT_WEIGHTS.copy()
        candidate.update({key: float(value) for (key, _), value in zip(_GRID, values)})
        score, _ = _pairwise_accuracy(comparisons, candidate)
        assert score is not None
        distance = sum(abs(candidate[key] - DEFAULT_WEIGHTS[key]) for key, _ in _GRID)
        if score > best_score or (score == best_score and distance < best_distance):
            best, best_score, best_distance = candidate, score, distance
    return best


def calibrate(payload: Any) -> dict[str, Any]:
    comparisons = _validate_dataset(payload)
    trip_ids = sorted({item["trip_id"] for item in comparisons})
    _, pair_count = _pairwise_accuracy(comparisons, DEFAULT_WEIGHTS)
    if len(trip_ids) < MIN_TRIPS or len(comparisons) < MIN_COMPARISONS:
        return {
            "evaluation_id": "pairwise_preference_weight_calibration_v1",
            "status": "INSUFFICIENT_DATA",
            "reason": "requires at least 5 distinct trips and 10 comparison sessions",
            "trip_count": len(trip_ids),
            "comparison_count": len(comparisons),
            "pair_count": pair_count,
            "default_grouped_cv_accuracy": None,
            "calibrated_grouped_cv_accuracy": None,
            "recommended_weights": DEFAULT_WEIGHTS.copy(),
            "application_defaults_changed": False,
            "scope": "offline explicit pairwise feedback; synthetic fixtures do not establish user calibration",
        }

    baseline_correct = 0.0
    calibrated_correct = 0.0
    validation_pairs = 0
    for held_out_trip in trip_ids:
        train = [row for row in comparisons if row["trip_id"] != held_out_trip]
        validation = [row for row in comparisons if row["trip_id"] == held_out_trip]
        fitted = _fit_weights(train)
        baseline_score, count = _pairwise_accuracy(validation, DEFAULT_WEIGHTS)
        fitted_score, _ = _pairwise_accuracy(validation, fitted)
        assert baseline_score is not None and fitted_score is not None
        baseline_correct += baseline_score * count
        calibrated_correct += fitted_score * count
        validation_pairs += count

    baseline_cv = baseline_correct / validation_pairs
    calibrated_cv = calibrated_correct / validation_pairs
    full_fit = _fit_weights(comparisons)
    improved = calibrated_cv > baseline_cv
    return {
        "evaluation_id": "pairwise_preference_weight_calibration_v1",
        "status": "CALIBRATED" if improved else "KEEP_DEFAULTS",
        "trip_count": len(trip_ids),
        "comparison_count": len(comparisons),
        "pair_count": validation_pairs,
        "default_grouped_cv_accuracy": round(baseline_cv, 6),
        "calibrated_grouped_cv_accuracy": round(calibrated_cv, 6),
        "candidate_fitted_weights": full_fit,
        "recommended_weights": full_fit if improved else DEFAULT_WEIGHTS.copy(),
        "application_defaults_changed": False,
        "cross_validation": "leave-one-trip-out; comparisons from one trip never cross train/test",
        "fixed_weights": {
            "plan_change_cost": DEFAULT_WEIGHTS["plan_change_cost"],
            "unfilled_visit_penalty": DEFAULT_WEIGHTS["unfilled_visit_penalty"],
        },
        "scope": "offline explicit pairwise feedback; synthetic fixtures do not establish user calibration",
    }


def run_synthetic_demo() -> dict[str, Any]:
    """Exercise the fitting path with generated choices, never real-user data."""
    comparisons = [
        {
            "trip_id": f"synthetic-trip-{trip_index}",
            "selected_candidate_id": "preferred",
            "candidates": [
                {
                    "candidate_id": "preferred",
                    "features": {
                        "plan_change_cost": 6,
                        "route_time_delta_min": 30,
                        "time_shift_min": 0,
                        "preference_penalty": 0,
                        "unfilled_visit_penalty": 0,
                    },
                },
                {
                    "candidate_id": "fast",
                    "features": {
                        "plan_change_cost": 6,
                        "route_time_delta_min": 0,
                        "time_shift_min": 0,
                        "preference_penalty": 3,
                        "unfilled_visit_penalty": 0,
                    },
                },
            ],
        }
        for trip_index in range(6)
        for _ in range(2)
    ]
    report = calibrate({"schema_version": 1, "comparisons": comparisons})
    report["input_source"] = "SYNTHETIC_DETERMINISTIC"
    report["scope"] = "generated choices exercise the fitter only; not empirical user calibration"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = calibrate(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
