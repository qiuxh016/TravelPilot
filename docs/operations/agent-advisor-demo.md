# Existing-trip Advisor Agent demo

This runbook demonstrates one bounded task: have the Advisor infer intent from an existing trip, proactively produce a schedule draft, surface useful follow-up choices, and apply the plan only after explicit confirmation.

## What this demo proves

- The Advisor reads places from the selected trip instead of asking the user to re-enter them.
- The Agent can proceed from an open request to a usable first draft without requiring a rigid prompt format, while preserving follow-up choices such as filling a meal gap.
- Route evidence, planner validation and warnings are visible in the conversation workspace. The demo can also query a driving route between two saved places and labels the fake-map result as simulated.
- A draft does not modify the formal itinerary. Explicit confirmation creates the next plan version.
- Stale drafts, overlapping fixed appointments and insufficient schedule capacity stop without an Agent write.

This demo uses a scripted local OpenAI-compatible response stub, Fake Maps and the deterministic local weather fixture (`WEATHER_PROVIDER=fake`). It demonstrates the execution and approval flow without external model, map or weather requests; it does not measure live-provider quality.

## Start the local services

From the repository root, start the database services and apply migrations:

```powershell
docker compose up -d postgres redis
docker compose ps
.\.venv\Scripts\alembic.exe upgrade head
```

In a separate PowerShell window, start the local response stub:

```powershell
.\.venv\Scripts\python.exe scripts/local_openai_chat_stub.py --host 127.0.0.1 --port 8002
```

In another window, start the API with test-only settings for this process. Use port 8001 for the complete browser run below. These assignments do not edit `.env`:

```powershell
$env:APP_ENV='development'
$env:SMTP_HOST=''
$env:SMTP_FROM_EMAIL=''
$env:EMAIL_SEND_LIMIT_PER_HOUR='10000'
$env:MAPS_PROVIDER='fake'
$env:WEATHER_PROVIDER='fake'
$env:LLM_API_KEY='travelpilot-local-test-key'
$env:LLM_MODEL='travelpilot-test'
$env:LLM_BASE_URL='http://127.0.0.1:8002/v1'
$env:WEB_BASE_URL='http://localhost:3001'
$env:WEB_ALLOWED_ORIGINS='http://localhost:3001'
.\.venv\Scripts\python.exe -m uvicorn travelpilot_api.main:app --host 127.0.0.1 --port 8001
```

Point the Web app's `NEXT_PUBLIC_API_URL` at `http://localhost:8001` and start it in a fourth window:

```powershell
$env:NEXT_PUBLIC_API_URL='http://localhost:8001'
pnpm --dir apps/web dev -- --hostname localhost --port 3001
```

Open the exact Local URL printed by Next.js. If port 8001 is already occupied, inspect the owning process first; if choosing another API port, update `NEXT_PUBLIC_API_URL` and the API's `WEB_ALLOWED_ORIGINS` consistently before starting the servers.

## Walk through the task

1. Register a temporary account and verify it through the development mailbox shown by the API.
2. Create a trip with at least two saved places and generate its initial plan.
3. Open **AI 旅行顾问**, start a conversation and select that trip.
4. Ask: `看看时间怎么安排比较合适`.
5. Inspect the generated draft, action trace, route evidence, plan warning and follow-up choices. Verify the current plan version has not changed.
7. Ask `从人民公园到武侯祠怎么坐地铁`. With AMap-backed saved places containing city codes, inspect the simulated or verified transit route card and its segments. With fake maps, the route is labeled simulated; this does not verify live AMap routing. Intercity rail and flights are not supported by this route slice.
8. Select **确认并加入行程**. If a saved hard preference conflicts with a selected place, review the conflict card and explicitly approve an exception for this plan before confirming. Verify the trip has exactly one new plan version.

To replay the edge cases automatically, run this test with the local API and stub above:

```powershell
$env:TRAVELPILOT_TEST_API_URL='http://localhost:8001'
$env:PLAYWRIGHT_BASE_URL='http://localhost:3001'
$env:TRAVELPILOT_TEST_STUB_URL='http://127.0.0.1:8002'
pnpm --dir apps/web exec playwright test e2e/advisor-existing-trip.spec.ts
```

The Playwright case covers proactive draft creation, memory conflict and explicit override, successful confirmation, stale-plan changes, fixed-appointment conflict, an infeasible one-day schedule, transit and route evidence, and pending-task invalidation. It creates a temporary account and deletes it in its cleanup path.

The elevated email-send budget is scoped to this local test API process. Without it, a previously used local Redis email budget can prevent the development mailbox from creating a verification link; registration may still return `202`, so the browser test fails later while trying to read that link. Keep this override out of production configuration.

## Operational limits

- Do not use the local stub result as a model quality score. Live-model comparisons need a separately approved request and token/cost cap.
- Fake maps are simulated provider data; route provenance in this demo is not live AMap evidence.
- Model-selected actions are bounded by the registered tools, schema checks, step limit and server-side trip ownership.
- Plan writes remain behind explicit confirmation and a fresh version/context check.
- This is a local demonstration workflow, not a production operations or availability review.
