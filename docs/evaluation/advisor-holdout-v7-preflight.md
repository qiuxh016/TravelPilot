# Advisor holdout v7 preflight

Updated: 2026-10-22

## Frozen set

- Dataset: `tests/evaluation/cases/advisor_multiturn_holdout_v7.json`
- Cases / decisions: 20 / 40, exactly two Agent-owned turns per case.
- Dataset raw SHA-256: `4575f6fdca381bd80283f319829aa2a64dc43a109ccf641dba642bba6acad87a`
- Canonical contract SHA-256: `65ee26f4ddca5b0546ecf2fd6c77bdeb3a0fb9cf099f3a2c4ce3c7f63c6b74aa`
- Families: discovery 3, itinerary 5, lodging 2, meal 3, preparation 1, route 3, search 1, weather 2.
- Every turn includes its carried user context, allowed tools, accepted safe action set, and a written adjudication note. The live runner supplies previous user/assistant actions and synthetic successful or failed tool observations as conversation history.
- Exact separation against 13 prior datasets: zero case-ID collisions and zero normalized-prompt collisions. Nearest prior wording was reviewed and revised where it was near-duplicate. Travel task families intentionally recur; this is not evidence of statistical or semantic independence.

## Evaluator contract

- `B2_AGENT_LOOP` records the model action, validates the tool envelope/arguments, and carries only local synthetic fixture results to the next turn.
- `B3_VERIFIED_RECOVERY_LOOP` executes that selected action through the production `run_constrained_loop`, registered production argument models, production search/weather/distance-matrix/route evidence verifiers, and an evaluation-local bounded safe-stop callback. It reports tool verification and safe stops separately from model action accuracy.
- No evaluator writes travel data. No live Maps or weather provider is called. B3 does not exercise the database-backed interrupted-action checkpoint restore or the authenticated Advisor HTTP workflow.
- `action_sequence_matches` means only that all scored action choices in a case matched an adjudicated action set. `end_to_end_task_completion_evaluated` is always false. Product-response quality is not assessed here.
- No matched B1 score is included: this harness does not provide an equivalent legacy fixed-workflow execution for all families.

## Frozen implementation inputs

| Input | SHA-256 |
|---|---|
| Dataset | `4575f6fdca381bd80283f319829aa2a64dc43a109ccf641dba642bba6acad87a` |
| Live evaluator | `7d3dc9dac5d124abfbd59f7783cb111f84118f762bac9088105d1db3693aa113` |
| Multi-turn contract validator | `59e9b516eff0f7afe9796e08b2cca4ccf2e8640753600ea442b68d4f7cbf01fd` |
| Production constrained loop | `908ddae699ba40768cd4ce2e6de39d62c886cc671926b15239455d22ef76640e` |
| Production action planner | `7e957c1b6b358759ac5d6b923aaf9a38c377a002e5fe062f159aefc6a842166b` |
| Production route query/evidence | `d0e434d4870a828f2de98d9e3be2a7befd15403f64074c109af6977c2463d290` |
| Production search verifier | `f77af812f508a8013b8863ccfeda15fb78bf4e50bbfe0eaabc62294cd19b08cf` |
| Production weather verifier | `a2a7fbebfd8df243a8d25350486f4200f2522289270d25e40bd76a0d6773573c` |

These hashes are checked against the frozen tree before the live run. Any source or dataset change requires a new preflight and a new holdout version.

The evaluator SHA above (`7d3dc9...`) is the exact source used for the scored run. A post-run compatibility fix narrowed the exclusive exposure marker to v7 only; other historical holdouts retain their previous test/replay behavior. Current evaluator SHA after this non-scoring-path fix: `d20ecbfa91c57ee0af1a8181506133449b979575f720452216d4b9d115a6478c`. The complete API/evaluation suite passes on this current source.

## Local preflight results

Commands from the repository root:

```powershell
\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts --dataset tests/evaluation/cases/advisor_multiturn_holdout_v7.json
\.venv\Scripts\python.exe -m pytest tests/evaluation/test_advisor_multiturn_live_eval.py tests/evaluation/test_advisor_multiturn_contracts.py tests/evaluation/test_constrained_agent_loop.py tests/evaluation/test_advisor_holdout_v6.py tests/evaluation/test_advisor_route_evidence.py -q
ruff check tests/evaluation/advisor_multiturn_live_eval.py tests/evaluation/advisor_multiturn_contracts.py tests/evaluation/test_advisor_multiturn_live_eval.py tests/evaluation/test_advisor_multiturn_contracts.py
```

Results before scoring: contract validation passed; **58 focused tests passed**; Ruff passed. This includes a one-time exposure-marker regression: the marker is written before scoring and blocks a second attempt. A separate local fixture replay sent all 40 adjudicated actions through the B3 constrained runtime: 36 completed tool/finish transitions, 4 user-input transitions, 25 verified tool calls, and no loop failures. Dedicated tests cover production route evidence acceptance, production search/weather/matrix verification, and route-provider timeout safe-stop recovery. No model requests were sent in preflight.

Post-run regression: **1,087 API/evaluation tests passed**, one existing Starlette/httpx deprecation warning; Ruff passed. This additionally verifies the exposed status and v7-only marker behavior against historical holdout tests.

## Authorized live run cap

The user approved the v7 action evaluation: at most 120 provider requests, at most 128 output tokens per request, and at most 15,360 requested output tokens total. The primary paired run evaluates all 20 cases in one invocation: 80 requests (20 cases × 2 turns × 2 arms), with a 10,240-token requested ceiling. The runner rejects larger selections or budgets and creates an exclusive local exposure marker before the first model call; the marker blocks any later scoring attempt in the same checkout. Stop if any run risks exceeding the approved request or token caps. Use only local synthetic Maps/weather observations. Do not read personal trip data or perform formal writes.

The tracked set remains `FROZEN_NOT_SCORED` until the run starts; after scoring, update the tracked status to `EXPOSED` and refuse every later scoring attempt. Preserve raw result files locally and publish only the aggregate report and failure audit.
