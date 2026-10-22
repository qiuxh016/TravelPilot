"""Small deterministic comparison for bounded itinerary conflict negotiation."""

from __future__ import annotations

import json
import time
from datetime import date

from travelpilot_core.engines.constraints.negotiation import (
    NegotiationActivity,
    negotiate_schedule,
)

DAY = date(2026, 11, 3)


def _activity(item_id: str, start: int, duration: int, **kwargs) -> NegotiationActivity:
    return NegotiationActivity(
        id=item_id,
        name=item_id,
        day=DAY,
        start_minute=start,
        duration_minute=duration,
        **kwargs,
    )


def _scenario_data():
    no_conflict = [_activity("a", 9 * 60, 60), _activity("b", 12 * 60, 60)]
    overlap = [_activity("a", 9 * 60, 90), _activity("b", 9 * 60 + 30, 60)]
    multi = [
        _activity("a", 9 * 60, 60), _activity("b", 9 * 60 + 30, 60),
        _activity("c", 14 * 60, 60), _activity("d", 14 * 60 + 30, 60),
    ]
    fixed = [
        _activity("appointment-a", 10 * 60, 90, fixed=True, required=True),
        _activity("appointment-b", 10 * 60 + 30, 60, fixed=True, required=True),
    ]
    fixed_and_flexible = [
        _activity("ticketed-show", 19 * 60, 90, fixed=True, required=True),
        _activity("optional-walk", 18 * 60, 120, optional=True, priority=2),
    ]
    lunch = [_activity("lunch", 15 * 60, 60, category="LUNCH", opens_at=9 * 60, closes_at=20 * 60)]
    closed = [_activity("museum", 9 * 60, 90, opens_at=11 * 60, closes_at=17 * 60)]
    unknown_route = [_activity("first", 9 * 60, 60), _activity("second", 12 * 60, 60)]
    return [
        ("no_conflict", no_conflict, {("a", "b"): 10, ("b", "a"): 10}, False),
        ("single_overlap", overlap, {("a", "b"): 0, ("b", "a"): 0}, True),
        ("independent_overlaps", multi, {
            (left.id, right.id): 0 for left in multi for right in multi if left.id != right.id
        }, True),
        ("fixed_appointments", fixed, {("appointment-a", "appointment-b"): 0}, False),
        ("fixed_and_flexible", fixed_and_flexible, {
            ("ticketed-show", "optional-walk"): 0,
            ("optional-walk", "ticketed-show"): 0,
        }, True),
        ("meal_window", lunch, {}, True),
        ("opening_hours", closed, {}, True),
        ("unknown_route", unknown_route, {}, False),
    ]


def _input_rejection_data():
    outside_trip = _activity("outside-trip", 10 * 60, 30)
    outside_trip = NegotiationActivity(
        **{**outside_trip.__dict__, "day": date.fromordinal(DAY.toordinal() + 1)}
    )
    return [("outside_trip_date", [outside_trip], DAY, DAY, "outside the trip dates")]


def run_evaluation() -> dict[str, object]:
    rows = []
    eligible = 0
    warning_only_solutions = 0
    single_change_solutions = 0
    negotiated_solutions = 0
    hard_invariant_violations = 0
    measured_ms = []

    for name, activities, routes, expected_repair in _scenario_data():
        started = time.perf_counter()
        result = negotiate_schedule(
            activities,
            start_date=DAY,
            end_date=DAY,
            travel_minutes=routes,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        measured_ms.append(elapsed_ms)
        alternatives = result.alternatives
        if expected_repair:
            eligible += 1
            negotiated_solutions += int(bool(alternatives))
            single_change_solutions += int(any(len(item.changes) == 1 for item in alternatives))
            warning_only_solutions += 0  # warning-only baseline never proposes a repair

        fixed_before = {item.id: item for item in activities if item.fixed or item.required}
        for option in alternatives:
            after = {item.id: item for item in option.activities}
            if any(after.get(item_id) != item for item_id, item in fixed_before.items()):
                hard_invariant_violations += 1
            independently_checked = negotiate_schedule(
                list(option.activities),
                start_date=DAY,
                end_date=DAY,
                travel_minutes=routes,
            )
            if not independently_checked.feasible or independently_checked.conflicts:
                hard_invariant_violations += 1

        rows.append({
            "scenario": name,
            "conflict_count": len(result.conflicts),
            "minimal_conflict_set": list(result.minimal_conflict_set),
            "alternatives": len(alternatives),
            "multi_change_alternatives": sum(len(item.changes) > 1 for item in alternatives),
            "search_states": result.explored_states,
            "search_truncated": result.search_truncated,
            "latency_ms": round(elapsed_ms, 3),
        })

    input_rejections = []
    for name, activities, start_date, end_date, expected_error in _input_rejection_data():
        try:
            negotiate_schedule(
                activities,
                start_date=start_date,
                end_date=end_date,
                travel_minutes={},
            )
        except ValueError as exc:
            rejected = expected_error in str(exc)
            if not rejected:
                hard_invariant_violations += 1
        else:
            rejected = False
            hard_invariant_violations += 1
        input_rejections.append({
            "scenario": name,
            "expected_rejection": expected_error,
            "rejected": rejected,
        })

    multi_row = next(row for row in rows if row["scenario"] == "independent_overlaps")
    two_change_solutions = int(multi_row["multi_change_alternatives"] > 0)
    return {
        "suite": "constraint_negotiation_synthetic_v2",
        "scenario_count": len(rows) + len(input_rejections),
        "repairable_scenarios": eligible,
        "baselines": {
            "warning_only_solutions": warning_only_solutions,
            "single_change_solutions": single_change_solutions,
            "single_change_success_rate": round(single_change_solutions / eligible, 4),
            "bounded_negotiator_solutions": negotiated_solutions,
            "bounded_negotiator_success_rate": round(negotiated_solutions / eligible, 4),
            "independent_conflicts_two_change_solution": bool(two_change_solutions),
        },
        "hard_invariant_violations": hard_invariant_violations,
        "latency_ms": {
            "mean": round(sum(measured_ms) / len(measured_ms), 3),
            "max": round(max(measured_ms), 3),
        },
        "cases": rows,
        "input_rejections": input_rejections,
        "limitations": [
            "Synthetic deterministic fixtures only; this is not user or model quality evidence.",
            "Warning-only and single-change baselines are diagnostic policies, not production B1.",
            "No map, weather, model, database, or formal-trip write is used.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_evaluation(), ensure_ascii=False, indent=2))
