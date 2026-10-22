# Advisor holdout v6 preflight

Date: 2026-10-22
Status: frozen before evaluation, locally validated, **scored and now exposed**
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

The live runner provides B2 action selection and a B3 prompt-policy-only arm with local fixture observations. It does **not** invoke production verifier/recovery, provide a matched fixed-workflow B1, or capture full user-facing Advisor replies.

The authorized diagnostic run completed in two batches of 10 tasks. It used the B2 Agent Loop and a B3 prompt-policy-only arm, not the production verifier/recovery implementation. The exact batch totals and limitations are recorded in [`advisor-holdout-v6-results.md`](advisor-holdout-v6-results.md). The prompts are exposed and must not be rescored as a blind set. A separate product-response capture and independent blind-rating protocol is still required for user-visible quality; the B1 comparison must use the actual legacy production workflow or be reported as unavailable, never replaced by a scripted score presented as comparable.

## Reproduction (local only)

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts `
  --dataset tests\evaluation\cases\advisor_multiturn_holdout_v6.json `
  --output $env:TEMP\advisor_v6_preflight.json
.\.venv\Scripts\python.exe -m pytest `
  tests\evaluation\test_advisor_holdout_v6.py `
  tests\evaluation\test_advisor_multiturn_contracts.py `
  tests\evaluation\test_advisor_unseen_multiturn_preflight.py -q
```

The freeze command was run before scoring. The freezer now refuses to overwrite this exposed dataset; create a new version for new scoring. Do not rerun the live evaluator on v6.
