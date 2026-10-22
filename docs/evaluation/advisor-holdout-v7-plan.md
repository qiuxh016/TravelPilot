# Advisor holdout v7 evaluation plan

Updated: 2026-10-22

## Why v7 is required

The v6 real-model run is complete and exposed. A post-run audit found at least six second-turn reference actions that conflict with tool evidence already present in the conversation or with the user's stated request. Preserve the raw v6 files for provenance, but do not use its action-match percentages or task counts as a model-quality comparison. Do not edit and rerun v6.

## Local implementation and preflight gates

Before any new model request:

1. Author a fresh 20-case, two-turn set. Reword and independently review scenarios; do not copy exposed v6 prompts or expected actions. Cover context continuation, corrections, schedule capacity and fixed stops, meal and allergy follow-ups, lodging and booking boundaries, route requests/failure, weather/preparation, and open-ended discovery.
2. Represent each turn's complete state: prior user turns, model actions, successful tool observations, failed tool observations, and any user-approved constraints. Preflight must prove that the expected action is valid for that exact state.
3. Use adjudicated `acceptable_actions` where more than one safe next action is valid. Validate tool arguments separately from action labels. Do not score a re-search as mandatory when the new turn only requests a caveat or changes no search constraint.
4. Separate three outcomes: action correctness, end-of-task completion, and user-facing response quality. The action harness must not label a task complete merely because a sequence of action strings matched.
5. Implement the B3 arm through the production constrained loop and production verifier/recovery path. If that path cannot be called from the evaluator, mark the arm unavailable; do not rename extra prompt text as production B3.
6. Provide a matched B1 only for task families that the actual legacy fixed workflow can execute with the same input/context and equivalent read-only observations. Report other families as not comparable instead of using a scripted score as a model baseline.
7. Freeze the dataset, task rubric, evaluated source revision, prompt/runtime hashes, provider/model identifier, and the per-run budget before scoring. Verify exact and semantic overlap review against prior exposed sets; exact text checks alone are insufficient.

## Proposed bounded real-model action run

- 20 fresh two-turn cases; B2 production Agent Loop and B3 production verifier/recovery where supported.
- Up to 80 primary decision requests plus at most 40 bounded verifier/recovery follow-up requests: **120 provider requests maximum**.
- At most **128 output tokens per provider request** and **15,360 requested output tokens total**. The runner must stop before exceeding either limit and record actual usage.
- Local synthetic read-only map/weather observations only. No formal itinerary writes, real map/weather requests, or use of personal trip data.
- Results are action/runtime diagnostics. They do not close user-visible response quality or independent human ratings.

The user authorized this v7 run: **up to 120 requests, 128 output tokens per request, and 15,360 requested output tokens total**. The request cap is a ceiling, not a target; the paired primary run uses 80 calls and a 10,240-token requested ceiling. The frozen set and local preflight are recorded in `docs/evaluation/advisor-holdout-v7-preflight.md`.

The evaluator B3 arm executes selected actions through the production constrained loop and production search/weather/matrix/route evidence validators. It uses local bounded safe-stop recovery and does not exercise database-backed interrupted-action checkpoint restoration or the full authenticated Advisor HTTP workflow; its results must be described with that boundary.

## Separate response-quality gate

Capture complete, bounded Chinese replies through the authenticated Advisor API using matched prompts, context, model settings, and source manifests. Generate an anonymized paired review sheet and keep the private arm key separate. Obtain independent human ratings for grounding, constraint safety, task progress, clarification quality, and transparency. The existing blind-review packager can prepare and summarize the sheet, but no human score may be reported until reviewers complete it. This product-response run and any additional model budget require their own concrete protocol and authorization.

## Exit state

P0 remains open until the reference-valid fresh action evaluation, supported matched B1, production B3, full product-response capture, failure analysis, and independent blind review are complete. Do not infer general competence from a small exploratory set. Keep production external trial and continuous-operations requirements out of this plan, as previously requested.
