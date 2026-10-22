# Advisor holdout v6 action evaluation

Date: 2026-10-22
Model: `deepseek-flash`
Dataset: `advisor_multiturn_holdout_v6`
Frozen canonical SHA-256 before scoring: `a8a6aa794ca3ab21e22c0e60edf0e20c8ee92aacf8c966985ebc7ad30bd58d67`

## Results

The 20 two-turn tasks were run in two batches of 10. Each Agent decision requested at most 128 output tokens. The run made **80 requests** total (two decisions × 20 cases × two arms), with a requested output ceiling of **10,240 tokens** and **2,572 provider-reported completion tokens**. Prompt usage was 48,272 tokens. No map, weather, database, or trip-write service was called.

| Arm | Matched actions | Complete tasks | Invalid actions | Tool/argument allowlist violations | Completion tokens |
|---|---:|---:|---:|---:|---:|
| B2 Agent Loop | 32/40 (80%) | 12/20 (60%) | 0 | 0 | 1,273 |
| B3 prompt-policy-only | 31/40 (77.5%) | 11/20 (55%) | 0 | 0 | 1,299 |

These are the original strict-label agreement counts on a small exploratory set. A post-run audit found reference-action defects that affect both arms, so the counts and the “complete task” column are **not valid comparative model-quality or product task-completion evidence**. In particular, the 0.5 percentage-point action gap must not be interpreted as a difference between the arms. The task column means only that every recorded action matched its authored label; the harness does not execute the full product task.

| Family | Cases | B2 matched actions / complete tasks | B3 matched actions / complete tasks |
|---|---:|---:|---:|
| Itinerary | 6 | 8/12 · 2/6 | 8/12 · 2/6 |
| Meal | 3 | 5/6 · 2/3 | 4/6 · 1/3 |
| Lodging | 3 | 4/6 · 1/3 | 4/6 · 1/3 |
| Transport | 3 | 6/6 · 3/3 | 6/6 · 3/3 |
| Weather | 2 | 4/4 · 2/2 | 4/4 · 2/2 |
| Preparation | 1 | 2/2 · 1/1 | 2/2 · 1/1 |
| Discovery | 2 | 3/4 · 1/2 | 3/4 · 1/2 |

## Post-run reference audit

The exposed set is preserved as-run and has not been rescored. Inspection of the frozen task contract and recorded tool history found at least six second-turn labels that are not consistent with the supplied state/evidence:

- Three scheduling turns expect another `GET_DISTANCE_MATRIX` after the previous turn already returned a verified matrix for the same saved places. The runner's own route-evidence gate makes `BUILD_SCHEDULE_DRAFT` available after that observation.
- The allergy follow-up expects another restaurant search after a candidate search, although the user specifically asks for an explicit allergen-safety limitation. The recorded `FINISH` actions correctly avoid implying that a search can verify kitchen cross-contamination.
- The lodging-budget follow-up expects another search after candidates were returned, although the user only clarifies that price/room type must be confirmed by the hotel and says not to book. A qualified `FINISH` is a valid next action.
- The open-ended discovery follow-up expects `FINISH` despite still lacking a departure city and dates, while its tool allowlist is empty. Asking a focused follow-up is valid and safer.

Other strict labels, including the meal-scope, direct-booking, and preparation continuations, also need independent adjudication before a future benchmark uses them. These defects can change the apparent arm ordering and explain several recorded misses. Keep the raw 80-request outputs for provenance, but do not use the percentages or family table to claim performance or compare B2 with B3. A corrected, newly versioned holdout must encode state transitions after each tool result and allow multiple safe next actions where the product contract permits them; v6 must remain exposed and must not be rescored.

## Method and evidence boundary

- This is a real-model **action-selection** run with local synthetic read-only tool observations. It did not execute the full Advisor API or measure the final Chinese reply shown to a traveler.
- `B3_PROMPT_POLICY_ONLY` adds evidence and recovery policy instructions to the model prompt. It does **not** run the production verifier/recovery path, so the comparison is B2 vs prompt-policy-only, not a full production B2/B3 ablation.
- The legacy fixed workflow is not a matched baseline for these seven task families. No B1 score is reported.
- The run provides request, token, model-output, and allowlist telemetry. Even the zero invalid-action and zero allowlist-violation counts do not prove the itinerary outcome or response quality is correct; the reference-label audit further limits action-match interpretation.
- Exact case-ID and normalized-prompt separation was checked against 11 historical datasets. The raw input file SHA-256 recorded by both batches is `ec46a987029d6fdb58ce966e8d1bcd71c61c93082fb1f66aa67a2c8199fd4572`; the frozen canonical hash above identifies the pre-run authored dataset. Semantic independence and statistical generalization are not established.
- Evaluation provenance recorded source revision `70fa9e973e14580f481b5bf72ace616c235951c0` and a dirty working tree. The local result files pin the evaluated dataset hash and source-file hashes.
- The raw per-action result files contain task prompts and remain local/ignored: `tests/evaluation/results/advisor_multiturn_live_holdout_v6_batch1.json` and `...batch2.json`.

The holdout is now marked `EXPOSED`; the live runner refuses to score it again, while contract validation still allows it to be loaded for audit and future overlap checks. A future holdout must include v6 in its reference registry.

## P0 status

**Still open.** The v6 action-match analysis is downgraded to non-comparative telemetry due to reference-label defects. Completion requires a corrected fresh holdout, a genuinely matched legacy B1 where that workflow applies, production verifier/recovery evaluation, full product-response capture, and independent blind ratings. The current run is not evidence that the Advisor can solve arbitrary travel requests or that users prefer its replies.
