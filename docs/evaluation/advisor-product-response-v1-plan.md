# Advisor product-response quality evaluation v1

Status: protocol and local budget gate prepared; live run not yet authorized.
Date: 2026-10-23.

## Question and scope

Measure whether the current authenticated Advisor experience gives concise,
grounded, useful Chinese replies across varied user requests and follow-ups.
This is a small diagnostic product-quality study. It does not establish general
travel competence, statistically significant superiority, real-world route or
business-hour accuracy, or production readiness.

The already scored v7 action holdout is exposed and excluded. This study scores
complete product replies through the authenticated API, not action JSON. It
uses temporary synthetic trips, local Fake Maps and local simulated weather;
formal trip writes and external map/weather calls are forbidden.

## Frozen design before any live call

- The 16 established product-service scenarios in `SCENARIO_ORDER` in
  `scripts/run_advisor_product_live_suite.py` (20 authenticated user messages).
  These are predefined diagnostic journeys, not a fresh unseen-task set. The
  separate v7 action holdout provides the unseen-task evidence and is excluded
  from this response-quality score.
- Coverage includes open-ended trip planning, multi-turn schedule edits,
  meal-only and lodging-only scope, attraction-plus-meal recommendations,
  weather preparation, route/weather failures, and explicit no-save requests.
- Exact scenario IDs, user-turn fingerprints, and focus areas are frozen in
  `tests/evaluation/cases/advisor_product_response_v1.json`. Expected outcome
  checks and synthetic setup are pinned by the evaluation source fingerprint.
  Do not revise cases after seeing responses; corrections require a new
  version and a separate report.
- One current production source revision and one named model/configuration.
  Report full source, prompt/runtime, case, and fixture fingerprints.
- Per user turn: server output ceiling 512 tokens. Per temporary Advisor
  session: server output ceiling 640 tokens. Run-wide actual completion-token
  ceiling and provider-requested-token ceiling: 10,240 tokens. Run-wide upstream
  request ceiling: 120;
  each upstream request is clamped to at most 512 output tokens. A fail-closed
  loopback forwarding proxy enforces request count, per-request and aggregate
  requested-token ceilings. The runner verifies its DeepSeek destination and
  fresh initial budget before launching any case.
- This ceiling is a hard stop, not a target. If the proxy cannot verify the
  actual model destination, request count, and requested-token sum, do not run.

The 120 provider-request cap is separate from the 20 HTTP user turns because one
Advisor turn can make multiple model calls. The proxy rejects a call before
forwarding when either remaining budget is insufficient. If that hard stop
interrupts a conversation, the live runner fails that case and does not report
a passing aggregate result; proxy counters remain available for diagnosis.

## Measures

Automated, per conversation: HTTP completion, complete assistant reply capture,
temporary-account deletion, no formal plan write, recorded model usage and
latency, provider-call and requested-token totals, source fingerprint, task
progress, surfaced choices, and safe handling of synthetic tool failures.

Independent reviewers score each conversation on a 1–5 scale for:

1. Grounding and factual restraint.
2. Preservation of stated trip conditions across turns.
3. Useful progress toward the user's request.
4. Concision and readability.
5. Appropriate questions and choices without repeated confirmation.
6. Transparency about unavailable or unverified information.

Reviewers also flag critical errors: invented evidence, lost hard constraints,
claiming a write that did not occur, or writing without confirmation. At least
two reviewers rate each anonymized conversation independently. Show per-case
ratings, agreement, missing ratings, critical-error counts, and the small-sample
limitation. Do not collapse critical errors into an average score.

## Privacy and artifacts

Use the existing temporary-account lifecycle and verify deletion for every
case. Review sheets omit model name, source traces, credentials, hidden
reasoning, and raw tool payloads. Keep the arm key and raw provider responses
local and ignored; commit only the protocol, fingerprints, and sanitized
aggregate report. Do not place personal trip data in cases.

## Completion gate

The protocol/harness phase is complete when the 16-case contract validates, the
proxy budget enforcement and fail-closed behavior pass local tests, and the
blind-review packet can be generated from a synthetic fixture report. The live
measurement phase is complete only after a separately authorized bounded run,
all required safe-cleanup checks, and independent reviewer ratings. A run
without human ratings is explicitly “response capture complete; human review
pending,” not a completed quality evaluation.

## Reproduction after authorization

Start the proxy in one PowerShell window, then run the suite in a second window.
The proxy accepts at most 120 upstream calls and 10,240 total requested output
tokens, clamping each request to 512 tokens. The suite's deterministic maximum
reservation for this protocol is 8,704 requested tokens; its actual-completion
ceiling is 10,240.

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.bounded_model_proxy `
  --upstream-base-url https://api.deepseek.com/v1 `
  --max-requests 120 `
  --max-requested-output-tokens 10240 `
  --max-output-tokens-per-request 512
```

```powershell
$env:LLM_BASE_URL = 'http://127.0.0.1:8022/v1'
.\.venv\Scripts\python.exe scripts\run_advisor_product_live_suite.py `
  --output tests\evaluation\results\advisor_product_response_v1_20261023.json `
  --per-turn-token-cap 512 `
  --session-token-cap 640 `
  --aggregate-token-cap 10240 `
  --require-bounded-proxy
```

The model key and model name must already be configured in the local environment
or `.env`; never paste either into a command or report. These commands make real
model calls and must only be run after separate approval. They do not call Maps
or Weather and do not write formal trip data.
