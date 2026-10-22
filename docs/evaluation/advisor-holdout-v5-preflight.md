# Advisor holdout v5 preflight

Date: 2026-10-22  
Status: frozen, not live-model scored  
Dataset: `tests/evaluation/cases/advisor_multiturn_holdout_v5.json`  
SHA-256 (canonical JSON): `17e948b395111c74979b05c8df3bb0eee5b0bf95fb84b9ce91b5554718fcc295`

## Coverage and evidence

The set contains 20 two-turn tasks (40 user turns) across schedule, meal, lodging, transport, weather, preparation, discovery and open consultation. Every route task supplies at least two stable `known_places` IDs; selected endpoint IDs refer to those records. Schedule cases carry coordinates for every saved stop. The contract and route-fixture preflight pass before any provider request.

Holdout separation was checked against 11 earlier datasets: zero case-ID overlap and zero exact normalized-prompt overlap. This establishes exact separation only; it does not establish semantic independence. The prompts have not been sent to a live model. The local scripted replay is explicitly an in-sample diagnostic, not a real-model score or a valid product baseline. It recorded 34/40 expected actions for B1, 23/40 for both scripted B2 and B3, and one B2 allowlist violation on the ambiguous replacement case. The violation is retained as a diagnostic signal; the real action runner's server allowlist remains authoritative.

## Live-run budget

The v4 authorization covered at most 120 requests and 15,360 requested output tokens. Its v4 run used 81 requests and 4,474 output tokens, leaving 39 requests and 10,886 tokens under that authorization. A complete B2/B3 run on v5 needs at least 80 requests (20 cases × 2 turns × 2 arms), with a hard cap of 120 to allow bounded B3 recovery; at 128 output tokens per request, the maximum requested ceiling is 15,360 tokens. The remaining 39-request allowance cannot complete the paired run. Do not start a partial run under this frozen protocol.

This action-only runner does not capture the final user-facing response composed by the full Advisor service. A separate product-response comparison and independent blind rating still need a defined sample and budget before P0 can pass.

## Local checks

```powershell
.\.venv\Scripts\python.exe tests/evaluation/freeze_advisor_holdout_v5.py
.\.venv\Scripts\python.exe -m tests.evaluation.advisor_multiturn_contracts `
  --dataset tests\evaluation\cases\advisor_multiturn_holdout_v5.json
.\.venv\Scripts\python.exe -m pytest `
  tests/evaluation/test_advisor_holdout_dataset.py `
  tests/evaluation/test_advisor_unseen_multiturn_preflight.py `
  tests/evaluation/test_advisor_multiturn_contracts.py -q
```

No live model, maps, weather or travel database is accessed by these commands.
