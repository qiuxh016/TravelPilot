# Context quality and reliability plan

**Status:** complete (2026-10-23). The new-task three-policy comparison, result adjudication, reliability checks, isolated PostgreSQL persistence verification, and CI workflow are complete. The live comparison did not establish a reliable policy improvement, so the existing production context policy was retained.

## Goal

Improve the Advisor's ability to carry user intent across turns while preserving current trip facts, confirmed choices, applicable preferences, and fresh tool evidence. Measure whether changes improve task outcomes without increasing unnecessary questions, invalid actions, latency, or token cost.

This replaces the public-benchmark follow-up as the next evaluation priority. The TREK compatibility audit found no fair whole-task subset for the current planner. Do not run a nominal TREK score by dropping unsupported dimensions.

## Existing capabilities to reuse

- Task-specific contracts and allowlisted tools: `apps/api/src/travelpilot_api/advisor/task_contract.py` and `packages/core/src/travelpilot_core/agents/loop.py`.
- Recent visible conversation bounded to six messages and 400 characters per message: `apps/api/src/travelpilot_api/advisor/conversation.py`.
- Server-authored stage observations with a bounded goal, trip facts, completed-stage summaries, and fixed stage contracts: `apps/api/src/travelpilot_api/advisor/turn_observation.py`.
- Preference and trip snapshots, context revisions, candidate selection preservation, and profile-memory fingerprints: `apps/api/src/travelpilot_api/advisor/context.py`.
- Session and turn token budgets: `apps/api/src/travelpilot_api/advisor/session_budget.py` and `apps/api/src/travelpilot_api/advisor/budgets.py`.
- Combined stage traces with statuses, failure codes, transitions, latency, tokens and estimated cost: `apps/api/src/travelpilot_api/advisor/trace.py`.
- Scoped decision/action checkpoints exist for selected workflows. They do not promise arbitrary full-turn restoration; retain that distinction in claims.

## Phase 1 — Freeze an evaluation protocol

Create a new, private-to-the-project synthetic multi-turn set with 20–30 tasks. Cover context continuation, short or ambiguous references, user corrections, preserved earlier choices, task switching, missing facts, preference conflicts, stale evidence, provider failures and cancellation. Include multiple phrasings for the same intent and avoid copying exposed holdout prompts.

For each turn, freeze the visible conversation, server-owned trip state, active memory, valid evidence, authorized tools, expected safe action, expected preserved state, and unacceptable outcomes. Separate action correctness from final reply quality.

Compare three context policies on identical inputs:

1. **Recent-history baseline:** current bounded conversation policy.
2. **Structured context:** current task contract plus current trip/session state and relevant recent turns.
3. **Selective context:** structured context plus only applicable memories and fresh evidence, with explicit provenance and a token budget.

Do not give a policy extra tools, facts, or output budget. Freeze model version, decoding settings, system instructions and tool schema across arms. Run local schema and input-equivalence preflight before any real-model call.

**Preflight checkpoint (2026-10-23):** A new 20-case / 40-decision set is frozen at `tests/evaluation/cases/advisor_context_quality_v1.json`. It was checked against 28 existing case files: zero exact normalized prompt overlaps. Every arm receives the same task, goal, current message, trip state, dynamically valid tool schemas, safety contract, visible history, and synthetic tool observations; only supplemental-context inclusion/format differs. Visible history follows production's last-six-message / 400-character limit, and each serialized planner input is checked against its 4,000-character truncation boundary. Local tests verify selection, arm equivalence, context separation, and case consistency. The planned full comparison is 120 sequential model decisions, with at most 256 output tokens each (30,720 reserved output tokens total). This is protocol/preflight evidence only; no model was called and no quality score exists yet. Case SHA-256: `c1425043b75018dfd113889001ad7f894abe7910099973fe60bd95aea3b2006e`.

**Live result (2026-10-23):** DeepSeek Flash completed 120 scored requests (40 decisions per arm). One earlier request returned but was lost when the first runner version mishandled the `TokenUsage` dataclass. Including that request, the cumulative total was at most 121 requests and 3,804 output tokens (3,548 reported plus a 256-token reserve), within the separately approved 121-request / 30,976-token ceiling. The run used only synthetic local tool observations and did not call maps/weather services or write travel data. The resumable runner now records a local per-decision checkpoint.

The aggregate analysis is committed at `tests/evaluation/results/advisor_context_quality_v1_analysis.json`; raw model responses and checkpoints remain local. Results: recent-history baseline 26/40 action matches and 11/20 complete two-turn sequences; structured context 27/40 and 11/20; selective context 27/40 and 11/20. Selective context improved paired action match rate by 2.5 percentage points, but its case-bootstrap 95% interval was -5 to +10 points. At the complete-sequence level it had one case win, one loss, and 18 ties versus baseline (two-sided exact McNemar p=1.0). This small synthetic evaluation therefore does not show a reliable task-success improvement.

**Adjudication and metric caveats:** `ctxq-v1-12-preserve-lodging` has a fixture mismatch: the trip says Guangzhou while the saved places carry Shanghai coordinates. Excluding it leaves action-match rates of 68.4%, 71.1%, and 71.1% for baseline, structured, and selective policies, while all remain at 11/19 complete sequences. `ctxq-v1-15-cancel-pending` may have an overly conservative expected `ASK_USER` label for an open request to discover senior-friendly places in Yunnan; excluding it yields 10/19, 10/19, and 11/19 complete sequences, respectively. These are sensitivity checks, not replacements for the frozen primary result. The raw `allowlist_violations` aggregate included planner failures that returned no action. Corrected accounting shows no unallowlisted tool calls; no-valid-action responses were 2/40, 1/40, and 0/40, and invalid returned actions were 2/40 in each arm. The action metric compares a single next action to a frozen label; it does not rate final reply quality, and no independent human blind rating was run.

## Phase 2 — Implement only measured context gaps

If the baseline exposes failures, implement an incremental selector that:

- preserves latest authoritative trip/plan state and explicit user corrections;
- resolves references against recent turns and current selected candidates;
- includes a memory only when relevant and authorized;
- includes provider evidence only when verified, scoped to the current trip/task, and fresh;
- records compact provenance for included and omitted context without logging raw private conversation by default;
- enforces deterministic input limits and never changes tool permissions or approval boundaries.

The selector must fail closed on stale/conflicting state and must not infer approval, dates, place identity, or hard constraints from a preference memory. **Outcome:** no production selector change was justified by this comparison. The selective arm had no complete-sequence gain overall and the paired confidence interval includes no effect. Keep the current production policy; improve case fixture/adjudication review before any new comparison.

## Phase 3 — Evaluate and diagnose

Primary metrics: safe next-action accuracy, complete multi-turn task success, preservation of prior confirmed choices, unnecessary clarification rate, invalid/unsupported action rate, and hard-constraint violations.

Efficiency metrics: input/output tokens, model/tool latency, retry count and estimated cost. Report paired case-level results and confidence intervals; do not claim improvement from one aggregate score alone. Independently rate full replies for grounding, clarity, useful progress and appropriate uncertainty.

For failures, connect task and stage traces through request/task identifiers and classify failures as context omission, stale evidence, model decision, tool/provider, verifier, persistence/concurrency, or presentation. Keep user text and provider payloads out of default telemetry; export only redacted, bounded artifacts. **Outcome:** the live result was reviewed by paired case and the above caveats were recorded. Independent human response-quality scoring remains outside this completed action-selection evaluation.

## Phase 4 — Targeted recovery, concurrency and CI

Use failure evidence to choose a small set of fault-injection cases: interruption before/after a checkpoint, duplicate request, provider timeout, context revision during a pending turn, and concurrent edits to one trip. Verify the formal plan version and confirmed meal/lodging/place selections after retry or conflict handling.

Add a minimal GitHub Actions workflow for Ruff, API/evaluation tests, web type-check/build, and isolated PostgreSQL migration/integration checks. Current checkout has no `.github/workflows`; use service containers only if they can run deterministically without external provider credentials.

**Outcome:** recovery, idempotency, concurrency, and plan-state tests passed (35 targeted tests); the full Python suite passed (1,114 tests); web TypeScript and production build passed. An ephemeral local pgvector/PostgreSQL 17 instance applied all Alembic migrations and passed the lodging persistence check across context refresh, event replan confirmation, a fresh-session reload, and plan v2 refresh. Added `.github/workflows/ci.yml` to repeat scoped Ruff, Python tests, web type-check/build, and the isolated PostgreSQL integration in CI. Repo-wide Ruff still reports eight pre-existing issues in two older evaluation files; the CI lint gate is scoped to application/core and the active evaluation files, all of which passed.

## Exit gates

- Frozen tasks pass format, provenance, arm-equivalence, and no-history-overlap checks.
- All policies receive identical authorized facts, tools, and budgets.
- The selected context policy improves a predeclared primary quality metric or reduces cost without a material safety/task-success regression; otherwise keep the current policy and document the null result.
- Checkpoint/retry/concurrency scenarios preserve confirmed plan state or return an explicit stale/conflict outcome.
- CI runs the declared local gates and reports failures without secrets or user travel data.
- Any live-model comparison uses a separately approved request/token ceiling; synthetic-only evidence is labeled as such.

All exit gates are satisfied: the task set and arm protocol were preflighted; the live comparison stayed within its approved ceiling; the null/uncertain result led to retaining production behavior; recovery and persistence checks passed; and the CI workflow contains the declared gates. This closes the plan without claiming production operations or human-rated response quality.

## Non-goals

No public benchmark score, Agent framework migration, multi-agent rewrite, vector database, Langfuse deployment, or broad full-turn recovery rewrite. Open deployment/operations remain outside this phase unless separately prioritized.
