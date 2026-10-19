"""Bounded, server-authored observations for turn-level stage decisions."""

from typing import Any

from travelpilot_api.advisor.turn_plan import AdvisorTurnPlan


def build_stage_selection_observation(
    *,
    user_goal: str,
    destination_available: bool,
    dates_available: bool,
    saved_place_count: int,
    confirmed_stop_count: int,
    completed_stage_observations: dict[str, Any],
    plan: AdvisorTurnPlan,
) -> dict[str, Any]:
    """Build the same bounded decision context at every stage boundary.

    This contains only the current task, compact trip facts, completed stage
    summaries supplied by trusted services, and the deterministic stage
    contracts. It cannot authorize a new stage or alter its tools.
    """
    completed = {}
    allowed_summaries = {
        "search_weather": {
            "candidate_count": "count",
            "search_failures": "count",
            "weather_available": "boolean",
            "weather_failures": "count",
        },
        "route": {"route_available": "boolean", "route_failures": "count"},
        "schedule": {"candidate_count": "count", "warning_count": "count"},
    }
    for name, facts in list(completed_stage_observations.items())[:3]:
        schema = allowed_summaries.get(name) if isinstance(name, str) else None
        if schema is None or not isinstance(facts, dict):
            continue
        summary = {}
        for key, kind in schema.items():
            value = facts.get(key)
            if kind == "boolean" and isinstance(value, bool):
                summary[key] = value
            elif kind == "count" and isinstance(value, int) and not isinstance(value, bool):
                summary[key] = max(0, min(value, 1000))
        if summary:
            completed[name] = summary

    return {
        "user_goal": str(user_goal)[:500],
        "trip_facts": {
            "destination_available": bool(destination_available),
            "dates_available": bool(dates_available),
            "saved_places": max(0, min(int(saved_place_count), 1000)),
            "confirmed_stops": max(0, min(int(confirmed_stop_count), 1000)),
        },
        "completed_stage_observations": completed,
        "stage_contracts": {
            stage.name: {
                "task_type": stage.task_context.task_type,
                "allowed_tools": stage.task_context.allowed_tools,
                "approval_boundary": stage.task_context.approval_boundary,
                "depends_on": list(stage.depends_on),
            }
            for stage in plan.stages
        },
    }
