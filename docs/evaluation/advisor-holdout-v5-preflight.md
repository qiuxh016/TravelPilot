# Advisor holdout v5 preflight

Date: 2026-10-22  
Status: frozen and scored for B2/B3 action selection; end-user response quality remains unscored
Dataset: `tests/evaluation/cases/advisor_multiturn_holdout_v5.json`  
SHA-256 (canonical JSON): `17e948b395111c74979b05c8df3bb0eee5b0bf95fb84b9ce91b5554718fcc295`

## Coverage and evidence

The set contains 20 two-turn tasks (40 user turns) across schedule, meal, lodging, transport, weather, preparation, discovery and open consultation. Every route task supplies at least two stable `known_places` IDs; selected endpoint IDs refer to those records. Schedule cases carry coordinates for every saved stop. The contract and route-fixture preflight pass before any provider request.

Holdout separation was checked against 11 earlier datasets: zero case-ID overlap and zero exact normalized-prompt overlap. This establishes exact separation only; it does not establish semantic independence. The prompts have not been sent to a live model. The local scripted replay is explicitly an in-sample diagnostic, not a real-model score or a valid product baseline. It recorded 34/40 expected actions for B1, 23/40 for both scripted B2 and B3, and one B2 allowlist violation on the ambiguous replacement case. The violation is retained as a diagnostic signal; the real action runner's server allowlist remains authoritative.

## Live action-selection result (2026-10-22)

The authorized DeepSeek `deepseek-flash` run used the production AgentRuntime with local synthetic tools. It completed all 20 tasks / 40 turns in each arm and used 83 of 120 allowed requests. The provider reported 4,725 output tokens; each request was capped at 128 output tokens and the aggregate requested ceiling was 15,360. The request cap was not exhausted and there was no provider-reported token overrun. No maps, weather services, or travel records were accessed or modified.

| Arm | Expected actions | Fully matched tasks | Invalid model actions | Tool failures | Hard-safety violations | Recovery decisions |
|---|---:|---:|---:|---:|---:|---:|
| B2 Agent Loop | 31/40 (77.5%) | 13/20 | 5 | 3 | 0 | 0 |
| B3 verifier/recovery | 32/40 (80.0%) | 14/20 | 5 | 3 | 0 | 3 |

B3 ended 33/40 turns as `SUCCEEDED`, versus 7/40 for B2; B2 hit its step limit in 24 turns and failed in 3, while B3 had no step-limit or failed statuses. This is evidence that the bounded recovery path improves runtime completion on this fixture set, while the action-match gain is only one decision and one fully matched task. Both arms had four malformed non-JSON outputs at the 128-token cap, including repeated failures on open consultation, plus one invalid tool action on a fixed-event case. The result warrants parser/format and action-contract follow-up; it does not show that the model can independently solve arbitrary travel tasks.

The run output is retained locally at the ignored path `tests/evaluation/results/advisor_unseen_multiturn_pilot_v5.json`; it records per-turn evidence, model/runtime source hashes, token usage, and tool transitions. The dataset is now exposed and must not be treated as a blind holdout in a rerun. B1 remains an offline scripted diagnostic and must not be compared to these live-model arms as a matched baseline. Final conversational reply usefulness/readability has not been evaluated or independently blind-rated.

After this run, commit `941ef11` shortened the action-selector contract to reduce output-format failures under the 128-token cap. The v5 scores predate that prompt change, so they are not evidence for its effectiveness; a fresh, unexposed set is needed for a valid check.

## Previous live-run budget

The v4 authorization covered at most 120 requests and 15,360 requested output tokens. Its v4 run used 81 requests and 4,474 output tokens. The v5 run used a separate user-approved allowance of at most 120 requests and 15,360 output tokens. Do not use unused capacity from either approval for a different evaluation protocol.

This action-only runner does not capture the final user-facing response composed by the full Advisor service. A separate product-response comparison and independent blind rating still need a frozen protocol and their own authorization before the full P0 exit gate can pass.

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
