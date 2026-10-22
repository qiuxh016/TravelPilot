# Advisor holdout v7 live action results

Run date: 2026-10-22 UTC  
Dataset status: `EXPOSED`  
Dataset SHA-256 at scoring time: `4575f6fdca381bd80283f319829aa2a64dc43a109ccf641dba642bba6acad87a`  
Raw provider-response artifact: local ignored file `tests/evaluation/results/advisor_multiturn_live_holdout_v7.json`

## Run boundary

- Provider/model: DeepSeek Flash (`deepseek-flash`, `api.deepseek.com`).
- Cases and decisions: 20 cases, two model decisions per case, evaluated in B2 and B3.
- Requests: 80 total (40 per arm); requested output ceiling 10,240 tokens; reported output use 2,303 tokens (B2 1,155, B3 1,148), and reported prompt use 46,364 tokens.
- Maximum reported completion for a single decision: 44 tokens. All 80 requests returned successfully.
- Tool evidence was synthetic and local. No Maps/weather provider, personal trip data, or formal itinerary write was used.

## Results

| Measure | B2 Agent Loop | B3 verified recovery loop |
|---|---:|---:|
| Adjudicated next actions matched | 39/40 (97.5%) | 39/40 (97.5%) |
| Full two-action sequences matched | 19/20 (95%) | 19/20 (95%) |
| Invalid actions | 0 | 0 |
| Tool allowlist violations | 0 | 0 |
| Verified local tool calls | not executed in B2 | 27 |
| Safe stops | not applicable | 1 |

The same model decisions were used for both arms, so action-selection scores are identical. B3 adds production-loop execution and evidence verification; this run does not show an action-quality gain from B3.

## Failure audit

Both arms missed `v7-meal-scope-05`, turn 1. The user supplied the destination, date, and request for local seafood near the coast. The model asked which coastal attraction they planned to visit instead of searching for seafood restaurants in the known destination. This was an unnecessary clarification and a task-progress failure. No other action missed its adjudicated safe-action set.

In `v7-route-outage-11`, B3 safely stopped after the synthetic route provider timeout rather than treating unavailable route evidence as success.

## Interpretation and limits

This is a small, exploratory **next-action** evaluation. A matched action sequence is not proof that the travel task was completed. The run did not capture full user-facing product responses, test database checkpoint restoration, exercise the authenticated Advisor HTTP workflow, or use a matched live B1 baseline. Exact prompt/ID overlap checks passed against 13 prior datasets; semantic or statistical independence is not established. Independent human response-quality ratings remain open. Do not claim arbitrary travel competence from these results.

The dataset is marked `EXPOSED`; its prompts and labels must not be reused as a fresh blind holdout. The runner's local exposure marker also blocks a second scoring run in the same checkout.
