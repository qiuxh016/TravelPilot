"""Canonical top-level task and bounded execution-stage plan for one Advisor turn."""

import re
from dataclasses import dataclass
from typing import Literal

from travelpilot_core.agents.loop import AgentTaskContext

from travelpilot_api.advisor.task_contract import build_agent_task_context, classify_search_task
from travelpilot_api.advisor.task_taxonomy import AdvisorTaskType, infer_task_type

StageName = Literal["SEARCH_WEATHER", "SCHEDULE", "ROUTE"]


@dataclass(frozen=True)
class AdvisorTurnStage:
    """One bounded domain workflow selected for the current turn."""

    name: StageName
    task_context: AgentTaskContext
    depends_on: tuple[StageName, ...] = ()


@dataclass(frozen=True)
class AdvisorTurnPlan:
    """Primary user outcome plus the scoped read-only/draft workflows it needs."""

    primary_task_type: AdvisorTaskType
    stages: tuple[AdvisorTurnStage, ...]

    def __post_init__(self) -> None:
        names = [stage.name for stage in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("Advisor turn stages must have unique names")

        known = set(names)
        dependencies: dict[StageName, set[StageName]] = {}
        for stage in self.stages:
            required = set(stage.depends_on)
            if stage.name in required:
                raise ValueError(f"Advisor stage {stage.name} cannot depend on itself")
            unknown = required - known
            if unknown:
                raise ValueError(
                    f"Advisor stage {stage.name} has unknown dependencies: {sorted(unknown)}"
                )
            dependencies[stage.name] = required

        completed: set[StageName] = set()
        while dependencies:
            ready = {
                name for name, required in dependencies.items()
                if required <= completed
            }
            if not ready:
                raise ValueError("Advisor turn stage dependencies must be acyclic")
            completed.update(ready)
            for name in ready:
                del dependencies[name]

    def stage(self, name: StageName) -> AdvisorTurnStage | None:
        return next((item for item in self.stages if item.name == name), None)

    def ready_stages(self, completed: set[StageName] | frozenset[StageName] = frozenset()) -> tuple[AdvisorTurnStage, ...]:
        """Return only authorized stages whose declared prerequisites are complete."""
        return tuple(
            stage for stage in self.stages
            if stage.name not in completed and set(stage.depends_on) <= set(completed)
        )


def ready_stage_names(
    plan: AdvisorTurnPlan,
    *,
    stage_statuses: dict[str, str],
    already_executed: set[str] | frozenset[str] = frozenset(),
) -> list[StageName]:
    """Return unrun stages whose dependencies completed successfully."""
    completed = {
        name for name, status in stage_statuses.items()
        if status == "SUCCEEDED"
    }
    return [
        stage.name for stage in plan.ready_stages(completed)
        if stage.name not in already_executed
    ]


def build_advisor_turn_plan(
    *,
    goal: str,
    planning_category: str | None,
    trip_available: bool,
    destination_available: bool,
    dates_available: bool,
    schedule_requested: bool,
    weather_requested: bool,
    preparation_requested: bool,
    discovery_requested: bool,
    search_requested: bool,
    route_requested: bool,
    available_search_tools: list[str] | tuple[str, ...] | set[str],
    known_facts: list[str] | None = None,
    missing_facts: list[str] | None = None,
) -> AdvisorTurnPlan:
    """Resolve a user-visible primary task and its bounded, compatible stages.

    A turn may contain a primary outcome and independent read-only evidence work.
    For example, route plus weather has a TRANSPORT primary task, a WEATHER
    evidence stage, and a separately scoped ROUTE stage. Persistent itinerary
    changes remain a proposal stage and still require the existing confirmation.
    """
    # A typed meal/lodging request stays scoped unless the user also clearly asks
    # to plan the broader trip. A meal-time question alone is not a sightseeing plan.
    schedule_enabled = (
        trip_available
        and schedule_requested
        and (
            planning_category is None
            or _explicit_broader_itinerary_request(goal)
        )
    )
    primary_task_type = infer_task_type(
        goal=goal,
        schedule_requested=schedule_enabled,
        planning_category=planning_category,
        weather_requested=weather_requested,
        discovery_requested=discovery_requested,
        search_requested=search_requested,
    )
    stages: list[AdvisorTurnStage] = []
    available = set(available_search_tools)
    facts = list(known_facts or [])
    missing = list(missing_facts or [])
    should_search_or_check_weather = destination_available and (
        search_requested or weather_requested and dates_available
    )
    if should_search_or_check_weather:
        search_task_type = classify_search_task(
            planning_category=planning_category,
            preparation_requested=preparation_requested,
            weather_requested=weather_requested,
            discovery_requested=discovery_requested,
        )
        stages.append(AdvisorTurnStage(
            name="SEARCH_WEATHER",
            task_context=build_agent_task_context(
                task_type=search_task_type,
                goal=goal,
                known_facts=facts,
                missing_facts=missing,
                available_tools=available,
                weather_requested=weather_requested,
            ),
        ))

    search_stage_available = any(stage.name == "SEARCH_WEATHER" for stage in stages)
    if schedule_enabled:
        stages.append(AdvisorTurnStage(
            name="SCHEDULE",
            task_context=build_agent_task_context(
                task_type="SCHEDULE",
                goal=goal,
                known_facts=facts,
                missing_facts=missing,
                available_tools=("get_distance_matrix", "build_schedule_draft"),
            ),
            depends_on=("SEARCH_WEATHER",)
            if search_requested and search_stage_available else (),
        ))

    if route_requested:
        stages.append(AdvisorTurnStage(
            name="ROUTE",
            task_context=build_agent_task_context(
                task_type="TRANSPORT",
                goal=goal,
                known_facts=facts,
                missing_facts=missing if trip_available else [*missing, "selected_trip"],
                # A route requires an owner-scoped trip. Keep the task visible
                # to the executor when the user is in free exploration, but do
                # not advertise a tool that cannot be safely bound yet.
                available_tools=("get_trip_route",) if trip_available else (),
            ),
            # A place search must finish before routes can use newly verified
            # candidates. A forecast-only check is independent of saved-place
            # routing and may be ordered by the constrained stage selector.
            depends_on=("SEARCH_WEATHER",)
            if trip_available and search_requested and search_stage_available else (),
        ))

    return AdvisorTurnPlan(primary_task_type=primary_task_type, stages=tuple(stages))


def _explicit_broader_itinerary_request(goal: str) -> bool:
    normalized = goal.casefold()
    terms = (
        "行程安排", "安排行程", "规划行程", "调整行程", "优化行程",
        "安排景点", "规划景点", "排一下景点", "安排游览", "规划游览",
        "整天行程", "全天行程", "一日游", "两日游", "多日游",
        "所有地点", "全部地点", "其他景点",
    )
    for term in terms:
        for match in re.finditer(re.escape(term), normalized):
            prefix = normalized[max(0, match.start() - 10):match.start()]
            if re.search(
                r"(?:不想|不要|不用|不需要|无需|别|先不)(?:再|去|给我)?"
                r"(?:安排|规划|排(?:一下)?|游览)?\s*$",
                prefix,
            ):
                continue
            return True
    return bool(re.search(
        r"分\s*(?:[一二两三四五六七八九十\d]+)\s*(?:天|日)\s*(?:来)?\s*(?:安排|排)",
        normalized,
    ))
