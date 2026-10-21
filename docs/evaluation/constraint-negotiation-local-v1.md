# Constraint negotiation local evaluation (2026-10-22)

## Scope

This is a deterministic seven-scenario evaluation of the bounded itinerary constraint solver. It compares a warning-only diagnostic, the solver's single-change alternatives, and its full bounded alternatives (including paired changes). All inputs are synthetic and fixed. The run does not contact a model, map, weather, database, or trip-writing service.

The source fingerprints for the recorded run are:

- Solver: `3defc50a4d26c75cd92275b67227aee0bf27258bfedaa5e0696ce9baa8585b78`
- Evaluation runner: `cc415978ca347981cc13c52e8dd2343df3d6be2679a92c13f8aa2e8a35305272`

## Results

| Measure | Result |
|---|---:|
| Scenarios | 7 |
| Scenarios with a known feasible repair | 4 |
| Warning-only repair success | 0/4 |
| Single-change repair success | 3/4 (75%) |
| Bounded negotiator repair success | 4/4 (100%) |
| Independent two-conflict case with a paired repair | Yes |
| Fixed/required invariant violations in surfaced alternatives | 0 |
| Solver latency | mean 1.091 ms; max 4.549 ms |

Cases cover no-conflict, a single overlap, two independent overlaps, colliding fixed appointments, lunch-window conflict, known opening hours, and missing route evidence. Fixed/fixed collision and missing-route cases correctly produce no alternative. Every surfaced option is rechecked against the full schedule constraints.

The single-change baseline is computed by filtering the same deterministic candidate generator to alternatives with one changed activity. The warning-only baseline reports conflicts without proposing repairs. They are diagnostic policies, not measured production B1 behavior. These results do not establish user preference, real-provider accuracy, model quality, or real-world travel feasibility. Latency measures local solver execution only.

## Reproduce

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m tests.evaluation.constraint_negotiation_eval
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_constraint_negotiation.py apps/api/tests/test_advisor_route_validation.py apps/api/tests/test_advisor_scheduling.py -q
```

The evaluation runner prints the per-case conflicts, alternatives, search states, truncation state, and solver latency as JSON.
