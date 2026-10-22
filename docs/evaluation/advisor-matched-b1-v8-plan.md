# Matched fixed-workflow baseline v8 plan

Updated: 2026-10-23  
Status: protocol prepared; not authorized or run.

## Purpose

The v7 action holdout has been exposed and cannot be reused. The existing fixed-workflow replay is deterministic and is not a live-model baseline. This follow-up is limited to task families the pre-Agent Advisor at revision `0c4a8998896bc5b27877ff9f49cfbf77ec0128d7` can actually perform: schedule a trip's already-saved places from trip dates and user constraints. It does not compare destination discovery, meal-only or lodging flows, weather, or open consultation because the legacy workflow does not provide equivalent handling for those tasks.

## Matched design

- Freeze four newly authored two-turn schedule tasks after checking exact and semantic overlap against v1-v7 holdouts, product-response prompts, and earlier exposed task suites.
- For each task, give B1, B2, and B3 the same trip dates, destination, pace, saved-place IDs and coordinates, user turns, and deterministic map observations. B1 is the fixed workflow at the pinned revision; B2 is the current production Agent Loop; B3 is the same current loop with the production verifier/recovery path.
- Run each arm in an isolated disposable account/session. Fake Maps and local route fixtures only; no live Maps/weather, personal trip data, or formal itinerary writes. Keep model, model settings, user-visible API path, and per-turn context identical where the arm supports them. Record a non-comparable result if the legacy arm cannot represent a state or constraint; do not fill gaps with a scripted B1 score.
- Evaluate completion of the requested schedule edit, inclusion of all saved places, date/time consistency, hard-constraint preservation, correct route-evidence handling, no unconfirmed write, and reply clarity. Report user-facing task outcomes separately from intermediate action choices and independent human ratings.
- Keep the case prompts and labels unavailable to the reviewer until a separate anonymized response packet is prepared. The v7 set and existing exposed product suite may inform coverage, but their prompts must not be copied.

## Proposed model budget for a later run

This is a new budget request, separate from the completed v7 authorization:

- Four cases × two user turns × three arms = 24 API turns.
- At most 512 completion tokens per API turn and 12,288 completion tokens across the run.
- Hard request ceiling: 48 provider requests, including any bounded internal action/verifier calls. The runner must stop before exceeding either the request ceiling or aggregate output-token ceiling.
- The run is diagnostic only (four tasks); it cannot establish statistical significance or arbitrary-task competence.

The run must not begin until the fresh case file, semantic-overlap review, baseline startup check, source/model manifests, token accounting, cleanup procedure, and refusal behavior are tested locally and the user authorizes this exact scope.

## Required local preflight before seeking run approval

1. Implement a frozen four-case dataset and a validator for both turns, state carryover, map fixture coordinates, and expected outcome invariants.
2. Prove the pinned B1 source can be launched against an isolated schema/database and can execute the supported schedule cases without modifying the current checkout or production data.
3. Run the same cases against B2/B3 with a loopback model stub; validate request/token ceilings, synthetic provider isolation, formal-write prevention, temporary-account cleanup, and full response capture.
4. Fail closed if the B1/B2/B3 inputs or state differ, a provider is not local, cleanup fails, or any budget ceiling is missing.

## Interpretation and remaining gates

Even a successful matched run closes only a small schedule-task comparison. P0 still requires independent ratings of the existing single-system quality sheet and a separate paired blind review if comparative user-facing quality is claimed. The 2026-10-20 single-system review packet is at `tests/evaluation/reviews/advisor_product_quality_review_user_authorized_20261020_v4.json`; ratings have not been entered. Production external trials and continuous operations remain outside the active plan.
