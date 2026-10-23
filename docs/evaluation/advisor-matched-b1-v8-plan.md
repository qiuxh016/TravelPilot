# Matched fixed-workflow baseline v8 plan

Updated: 2026-10-23  
Status: revised four-case matched diagnostic set and exact-prompt checks pass; pinned B1 authenticated API passes all four cases (8/8 turns proposed every confirmed stop and preserved each formal plan); B2/B3 production AgentRuntime passes the same four cases with a loopback stub (20 requests, 200 reported output tokens, zero formal writes); a comparable live-model runner and independent ratings remain; the new live run is not yet authorized.

## Purpose

The v7 action holdout has been exposed and cannot be reused. The existing fixed-workflow replay is deterministic and is not a live-model baseline. Source inspection and an authenticated local API probe found that the pre-Agent Advisor at revision `0c4a8998896bc5b27877ff9f49cfbf77ec0128d7` only proposes schedule changes for stops in an existing confirmed plan; a trip with wishlist entries alone returns no schedule proposal. The probe's first case did exactly that, so the original saved-places-only case setup was invalid for B1 and has been replaced. The revised comparison is limited to re-timing confirmed plan stops. It does not compare destination discovery, meal-only or lodging flows, weather, open consultation, or creating an initial schedule.

## Matched design

- Freeze four newly authored two-turn schedule tasks after checking exact and semantic overlap against v1-v7 holdouts, product-response prompts, and earlier exposed task suites. The current case file is `tests/evaluation/cases/advisor_matched_schedule_b1_v1.json`; it is explicitly a matched diagnostic set, not a claim of semantic independence from prior schedule-task families.
- For each task, seed the same confirmed initial plan from saved places, then give B1, B2, and B3 identical trip dates, destination, pace, place IDs and coordinates, user turns, and deterministic map observations. Verify the starting plan ID/version and ordered stops before each arm. B1 is the fixed workflow at the pinned revision; B2 is the current production Agent Loop; B3 is the same current loop with the production verifier/recovery path.
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

1. Implement a frozen four-case dataset and a validator for both turns, state carryover, map fixture coordinates, expected outcome invariants, and exact prompt non-reuse. **Prepared locally:** the dataset test checks four two-turn cases, confirmed-plan setup, coordinates, acceptance invariants, strict run limits, and exact prompt overlap against prior case JSON and registered product scenarios. It does not prove semantic independence.
2. Prove the pinned B1 source can be launched against an isolated schema/database and can execute the supported schedule cases without modifying the current checkout or production data. **Passed locally for all four cases:** a detached worktree at the pinned B1 revision migrated a disposable database and passed **22** interpretation unit tests. The authenticated API run seeded a confirmed plan separately for each case, then sent both schedule turns through the production endpoint using a loopback model stub and Fake Maps. All **8/8** turns returned `SCHEDULE_PLAN` suggestions for every confirmed stop on an in-range date; all four formal plan IDs/versions remained unchanged. Temporary accounts, database, API process, and worktree were removed; Redis DB 15 was returned to empty. The earlier wishlist-only probe is retained as the reason for requiring a confirmed-plan setup. This proves the bounded legacy-arm endpoint harness with a local model stub; it is not live-model evidence.
3. Run the same cases against B2/B3 with a loopback model stub; validate request/token ceilings, synthetic provider isolation, formal-write prevention, temporary-account cleanup, and full response capture. **Core-loop preflight passed and is repeatable:** `python -m tests.evaluation.advisor_matched_schedule_runtime_preflight` runs the production `AgentRuntime` for all four cases in both B2/B3 arms, using 20 loopback requests and 200 reported output tokens (128-token per-request cap; both below the 48-request/12,288-token ceilings). Both arms selected the expected route-matrix then schedule-draft action path for every case; all tool outputs were local fixtures and no formal write occurred. This validates the bounded runtime and action-harness path only: the stub chose actions from available tools and prior observations, so it provides no model-quality evidence and does not exercise authenticated product API cleanup. B1's separate authenticated product-API run covered all four cases as recorded above. The matching authenticated B2/B3 product path and disposable-account cleanup remain unverified.
4. Fail closed if the B1/B2/B3 inputs or state differ, a provider is not local, cleanup fails, or any budget ceiling is missing.

## Interpretation and remaining gates

Even a successful matched run closes only a small schedule-task comparison. P0 still requires independent ratings of the existing single-system quality sheet and a separate paired blind review if comparative user-facing quality is claimed. The 2026-10-20 single-system review packet is at `tests/evaluation/reviews/advisor_product_quality_review_user_authorized_20261020_v4.json`; ratings have not been entered. Production external trials and continuous operations remain outside the active plan.
