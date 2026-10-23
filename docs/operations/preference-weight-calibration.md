# Replanning preference-weight calibration

The replanner keeps hard feasibility checks separate from its weighted objective. The current defaults remain in the application until there is enough explicit user-choice data to justify a change.

## Input

Create a UTF-8 JSON file with schema version 1. Each comparison represents one user choosing among at least two feasible alternatives. Group all comparisons from the same trip under the same opaque `trip_id`; do not include names, messages, account IDs, or other personal data.

```json
{
  "schema_version": 1,
  "comparisons": [
    {
      "trip_id": "anonymous-trip-01",
      "selected_candidate_id": "candidate-a",
      "candidates": [
        {
          "candidate_id": "candidate-a",
          "features": {
            "plan_change_cost": 6,
            "route_time_delta_min": 25,
            "time_shift_min": 0,
            "preference_penalty": 0.2,
            "unfilled_visit_penalty": 0
          }
        },
        {
          "candidate_id": "candidate-b",
          "features": {
            "plan_change_cost": 6,
            "route_time_delta_min": 5,
            "time_shift_min": 15,
            "preference_penalty": 2.5,
            "unfilled_visit_penalty": 0
          }
        }
      ]
    }
  ]
}
```

Only feasible alternatives belong in a comparison. Feature values must use the same units and definitions as the planner objective. `selected_candidate_id` must be one of the displayed candidates. A trip may contribute multiple comparisons, but all of them stay together during validation.

## Run

```powershell
.venv\Scripts\python.exe -m tests.evaluation.preference_calibration feedback.json --output calibration-report.json
```

The tool requires at least 5 distinct trips and 10 comparison sessions. It uses leave-one-trip-out validation, compares candidate weights against current defaults, and searches a bounded grid for route-time, time-shift, and preference weights. Plan-change and unfilled-visit weights remain fixed. It returns `INSUFFICIENT_DATA` below the threshold and `KEEP_DEFAULTS` unless grouped validation improves. It never changes application configuration.

## Limits

The report's accuracy measures agreement with explicit pairwise choices, not travel satisfaction or causal quality. Synthetic fixtures verify the calibration code path only; only consented, de-identified user choices can provide empirical calibration evidence. Review sample coverage and segment results before proposing any application default change.
