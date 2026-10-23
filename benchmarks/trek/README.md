# TREK public benchmark compatibility audit

This folder isolates the TREK open benchmark investigation from TravelPilot's product tests and model evaluations.

## Current result

The current phase is a **compatibility audit only**, not a model/planner score. At the pinned upstream revision, all 800 TREK tasks require flights, hotels, and attractions; 261 also require rental cars. TravelPilot's direct deterministic itinerary planner currently schedules timed attraction candidates and can validate opening hours and optional route-time matrices. A strict whole-task gate therefore finds no fair complete-task subset.

The audit script downloads only `trek_queries.csv`; it does not read gold outputs, call an LLM/provider, call map/weather services, or write travel data. It preserves the complete task denominator and will fail closed if the upstream task count or shape changes unexpectedly.

## Reproduce

From the repository root in PowerShell:

```powershell
& .\.venv\Scripts\python.exe benchmarks\trek\audit_compatibility.py --refresh
```

The result is written to `benchmarks/trek/results/compatibility_audit.json`. The fetched task metadata is stored under `benchmarks/trek/data/` and ignored by Git because the audit report records its SHA-256 and exact source revision.

## Scope and fair-use rules

- Upstream repository: [TREK](https://github.com/TonyQJH/TREK-A-Travel-Reasoning-and-Evaluation-Kit-for-LLM-Agents-in-Complex-Trip-Planning)
- Pinned source commit: `6ceb4ebb2debd69c5c7c4ba34b5b17524756912b`
- Upstream declares code under MIT and dataset under CC BY 4.0; include attribution with redistributed data/results.
- TREK's synthetic prices, schedules, and availability are not real travel information.
- Do not describe this compatibility audit as a TREK score or as evidence of planning quality.
- Do not remove unsupported tasks from the official denominator after seeing model results.
- Any future partial-dimension study must be separately named, define its scoring projection before running, and report the full official task count plus the excluded dimensions.

## Next decision gate

Since no whole task currently fits the direct planner contract, do not spend model budget on a nominal TREK run. First assess whether a separate benchmark adapter is justified or whether a Chinese task benchmark better covers TravelPilot's actual supported workflows. Any live-model evaluation needs its own bounded authorization and frozen task selection.
