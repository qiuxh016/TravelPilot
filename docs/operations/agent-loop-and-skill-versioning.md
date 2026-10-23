# Agent Loop and Skill Version Control

## Planner execution boundary

The planner can call only `get_trip_context` and `get_distance_matrix` inside its bounded read-only loop. The configured model may request the distance matrix or finish when that evidence is not needed. The server supplies the trip ID and coordinates; model-provided arguments are not trusted. `AgentRuntime` still validates schemas, limits logical steps and attempts, and records tool traces. The API checks that context is trip-scoped and that a returned matrix is square before planning.

The plan itself is produced by the deterministic `PlanService`. Plan creation and the successful `AgentRun` checkpoint commit in the same database transaction. An `Idempotency-Key` can be reused after interruption: a completed run returns its existing plan; a failed run retries read-only work. A `RUNNING` run with a checkpoint updated in the last two minutes returns HTTP 409 to avoid duplicate concurrent execution; after that lease window it can be retried. Do not reuse a key with different preference text. The key is scoped to a trip and protected by a database uniqueness constraint.

The last 32 loop checkpoints contain phase names, tool names, counts and error codes. They do not contain place names, coordinates, or raw preference text. Tool trace redaction remains in effect. The LLM can choose among read-only observations only; it cannot write a trip, plan, reservation, or memory.

## Skill release and rollback

`packages/core/src/travelpilot_core/skills.py` contains the application version pins. A Skill change requires:

1. Increment the Skill frontmatter `version`.
2. Update the matching `_PINNED_VERSIONS` entry in code in the same change.
3. Run the applicability cases in `tests/evaluation/cases/scenarios.json` and the full Python suite.
4. Review the Skill diff as a prompt change; it never grants tool permissions.

To roll back, restore both the Skill file and its version pin from the known-good revision, then run the same checks. Runtime traces record the selected Skill name and version where it is applied.

## Evaluation boundary

`tests/evaluation/reproducible_experiment.py` runs two deterministic repetitions of each synthetic suite in separate Python processes. Both use fixed Python random/hash seeds, stable UUID5 fixtures, and blank LLM/AMap credentials. The report records process IDs, stable result digests, source/data SHA-256 hashes, the checked-out revision, dirty-worktree state, Python/platform, Provider mode, model, and token budget. It hashes the Planner decision/API implementation, tool contracts/adapters, Python dependency lock, and evaluation inputs. Run it with:

```powershell
.venv\Scripts\python.exe -m tests.evaluation.reproducible_experiment --output tests/evaluation/results/experiment_reproducibility_v18.json
```

`stable_outputs_match` verifies deterministic fixture behavior after excluding run timestamps. It does not establish LLM quality, real map accuracy, or deployment reliability. The weather category ablation compares old and current rules on the same two weather cases; report its counts only with this dataset and scope.

`tests/evaluation/live_skill_ablation.py` provides a separate synthetic-text Skill enabled/disabled experiment. It requires `--confirm-live-requests`, caps each response at the requested limit, and stores only extracted place summaries, response shape, approved provider metadata, and usage, not prompt or response text. v6 used 417 output tokens but two Skill-enabled responses failed because `confidence` was not a number. After adding a numeric-type instruction, v7 made six requests at a 160-token cap and used 422 output tokens; all six were schema-valid, and all three pairs succeeded. In both positive cases, Skill and baseline each achieved precision/recall 1.0 with two exact evidence matches. In the no-place case, both correctly returned no locations. Quality metrics were identical in this small set.

The extraction prompt now includes a concrete JSON example and explicitly requires `confidence` to be a JSON number from 0 to 1. DeepSeek Chat Completions supports `--thinking-mode disabled`; this option is provider-specific. v6 evidence showed that disabling thinking avoided the earlier output truncation but did not fix confidence types. v7 then validated the numeric-type instruction on these three short cases. The Skill prompt increased input usage from 737 to 1,121 tokens across the six requests while reducing output usage from 226 to 196 tokens; total usage increased by 354 tokens (963 baseline vs 1,317 with Skill). Three synthetic pairs are exploratory and do not establish a general quality or cost result.

If a new live comparison is authorized, keep the existing 160-token per-call and 960-token total cap unless a different budget is explicitly approved:

```powershell
.venv\Scripts\python.exe -m tests.evaluation.live_skill_ablation --confirm-live-requests --max-output-tokens 160 --thinking-mode disabled --output tests/evaluation/results/content_import_skill_live_ablation_v8.json
```

The v8 command still makes real model requests and therefore needs a new explicit call-budget authorization. DeepSeek documents that JSON mode can return empty content, recommends an explicit JSON example and a sufficient output cap, and supports disabling thinking on Chat Completions ([JSON Output](https://api-docs.deepseek.com/guides/json_mode/), [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)).
