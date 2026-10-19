"""Build the user-visible execution report for an Advisor turn."""

from typing import Any

from travelpilot_api.advisor.stage_execution import AdvisorTurnExecutor, summarize_stage_execution
from travelpilot_api.advisor.trace import combine_agent_traces
from travelpilot_api.advisor.turn_plan import AdvisorTurnPlan


def build_turn_agent_report(
    *,
    plan: AdvisorTurnPlan,
    executor: AdvisorTurnExecutor,
    stage_traces: dict[str, dict[str, Any] | None],
    orchestration: dict[str, Any] | None,
    supplemental_execution_order: list[str],
) -> dict[str, Any]:
    """Merge domain traces with shared lifecycle outcomes into a bounded report."""
    trace_by_stage = dict(stage_traces)
    for stage_name, trace in executor.traces.items():
        if trace_by_stage.get(stage_name) is None:
            trace_by_stage[stage_name] = trace

    executed_stage_order = [
        stage_name for stage_name in executor.execution_order
        if trace_by_stage.get(stage_name) is not None
    ]
    for stage_name in supplemental_execution_order:
        if trace_by_stage.get(stage_name) is not None and stage_name not in executed_stage_order:
            executed_stage_order.append(stage_name)
    for stage_name in (orchestration or {}).get("order", []):
        if trace_by_stage.get(stage_name) is not None and stage_name not in executed_stage_order:
            executed_stage_order.append(stage_name)
    for stage_name in ("SEARCH_WEATHER", "ROUTE", "SCHEDULE"):
        if trace_by_stage.get(stage_name) is not None and stage_name not in executed_stage_order:
            executed_stage_order.append(stage_name)

    primary_stage = next(
        (name for name in ("SCHEDULE", "ROUTE", "SEARCH_WEATHER")
         if trace_by_stage.get(name) is not None),
        None,
    )
    agent_trace = combine_agent_traces(
        [(name, trace_by_stage[name]) for name in executed_stage_order],
        primary_stage=primary_stage,
    )
    if executor.recovery:
        agent_trace = {
            **(agent_trace or {"transitions": []}),
            "recovery": {
                "status": "SAME_TASK_SAFE_RETRY",
                "interrupted_stage": executor.recovery.get("interrupted_stage"),
                "completed_stages": executor.recovery.get("completed_stages", []),
            },
        }

    stage_execution_report = summarize_stage_execution(plan, trace_by_stage)
    missing_stage_names = stage_execution_report["unexecuted_stages"]
    if missing_stage_names:
        agent_trace = {
            **(agent_trace or {"transitions": []}),
            "status": stage_execution_report["overall_status"] or "FAILED",
            "stage_statuses": stage_execution_report["stage_statuses"],
            "unexecuted_stages": missing_stage_names,
        }
    if orchestration and len(orchestration.get("order", [])) > 1:
        agent_trace = {
            **(agent_trace or {"transitions": []}),
            "turn_orchestration": {
                **orchestration,
                "decision_history": executor.selection_history,
            },
        }
    return {
        "trace_by_stage": trace_by_stage,
        "executed_stage_order": executed_stage_order,
        "agent_trace": agent_trace,
        "stage_execution_report": stage_execution_report,
        "missing_stage_names": missing_stage_names,
    }
