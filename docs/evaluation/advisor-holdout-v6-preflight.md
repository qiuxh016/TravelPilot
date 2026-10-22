# Advisor holdout v6 preflight

Date: 2026-10-22  
Status: frozen, locally validated, **not scored**  
Dataset: `tests/evaluation/cases/advisor_multiturn_holdout_v6.json`  
Canonical JSON SHA-256: `a8a6aa794ca3ab21e22c0e60edf0e20c8ee92aacf8c966985ebc7ad30bd58d67`

## Scope

The dataset contains 20 two-turn tasks (40 user turns) covering itinerary edits, referential follow-ups, user corrections, meal and lodging constraints, route clarification/failure, weather, preparation, and open-ended discovery. All tasks have two Agent-owned decisions and synthetic, read-only observations. Schedule coordinates and route endpoint IDs are grounded in the local fixture records.

The frozen file has zero case-ID overlap and zero exact normalized-prompt overlap with the 11 registered historical datasets. This is exact-match separation only; semantic independence is not established. Cases are exploratory and too small to establish arbitrary-task competence.

## Local checks

- Contract validation: **20 cases / 40 turns**.
- Exact ID and prompt separation: **0 / 0**.
- Route and schedule fixture evidence preflight: passed.
- Focused holdout/contract/preflight regression: **12 passed**.
- No model, maps, weather service, or trip database call was made.

## Scoring boundary

The existing live runner provides B2 and B3 action-selection arms with local fixture observations; it does **not** provide a matched production fixed-workflow B1 or capture full user-facing Advisor replies. Therefore, a B2/B3 run on v6 would be diagnostic only and would not close the P0 gate. The previous v5 authorization is exhausted and explicitly cannot be reused for this newly frozen protocol.

For the diagnostic B2/B3 action run, divide the 20 tasks into two frozen batches of 10. The runner's hard request ceiling would be 120 total calls, 128 output tokens per call, and at most 15,360 requested output tokens, using no live map/weather provider and no travel-data writes. A separate product-response capture and independent blind-rating protocol is still required for user-visible quality; the B1 comparison must use the actual legacy production workflow or be reported as unavailable, never replaced by a scripted score presented as comparable.

## Reproduction (local only)

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.freeze_advisor_holdout_v6
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts `
  --dataset tests\evaluation\cases\advisor_multiturn_holdout_v6.json `
  --output $env:TEMP\advisor_v6_preflight.json
.\.venv\Scripts\python.exe -m pytest `
  tests\evaluation\test_advisor_holdout_v6.py `
  tests\evaluation\test_advisor_multiturn_contracts.py `
  tests\evaluation\test_advisor_unseen_multiturn_preflight.py -q
```

The live runner requires a separate explicit authorization and must not be run as part of these local checks.
