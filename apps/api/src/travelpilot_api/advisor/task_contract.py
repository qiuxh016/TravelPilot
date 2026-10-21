"""Canonical task scopes and success contracts for the Advisor runtime."""

from travelpilot_core.agents.loop import AgentTaskContext

from travelpilot_api.advisor.task_taxonomy import AdvisorTaskType

_TASK_POLICIES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "DISCOVERY": (
        ("search_places", "get_trip_weather"),
        ("return verified candidates for the selected destination", "use trip weather only when requested", "ask for preferences when needed"),
        "READ_ONLY",
    ),
    "SEARCH": (
        ("search_places",),
        ("return verified candidates matching the request", "ask when required facts are missing"),
        "READ_ONLY",
    ),
    "MEAL": (
        ("search_places",),
        ("return verified food candidates only", "leave date and time choices to the user"),
        "READ_ONLY",
    ),
    "LODGING": (
        ("search_places",),
        ("return verified lodging candidates only", "do not book or imply availability guarantees"),
        "READ_ONLY",
    ),
    "TRANSPORT": (
        ("get_trip_route", "get_distance_matrix"),
        ("verify a route between owner-scoped saved places", "clarify unsupported modes"),
        "READ_ONLY",
    ),
    "SCHEDULE": (
        ("get_distance_matrix", "build_schedule_draft"),
        ("preserve fixed and reserved stops", "produce a verified nonpersistent schedule proposal"),
        "DRAFT_ONLY",
    ),
    "WEATHER": (
        ("get_trip_weather",),
        ("return a dated forecast with source and freshness", "state clearly when forecast data is unavailable"),
        "READ_ONLY",
    ),
    "PREPARATION": (
        ("get_trip_weather",),
        ("give conservative preparation advice", "ground weather-specific advice in a dated forecast"),
        "READ_ONLY",
    ),
    "MEMORY": (
        (),
        ("use only user-authorized travel memories", "ask before persistent memory changes"),
        # This task can discuss memories already present in the owner-scoped
        # context. It has no write tool; saving still goes through the separate
        # explicit-confirmation memory API and must not make the whole turn look
        # like an already-approved mutation.
        "READ_ONLY",
    ),
    "GENERAL": (
        ("search_places", "get_trip_weather", "get_trip_route"),
        ("answer from available travel evidence", "ask or state limits when evidence is insufficient"),
        "READ_ONLY",
    ),
}


def classify_search_task(
    *,
    planning_category: str | None,
    preparation_requested: bool,
    weather_requested: bool,
    discovery_requested: bool = False,
) -> AdvisorTaskType:
    """Choose the outcome policy for a read-only map/weather turn."""
    if planning_category == "FOOD":
        return "MEAL"
    if planning_category == "LODGING":
        return "LODGING"
    # Discovery can require both POI search and weather evidence, such as an
    # indoor alternative request on a rainy travel day. Its policy authorizes
    # both read-only tools, so don't narrow it to weather/preparation alone.
    if discovery_requested and (preparation_requested or weather_requested):
        return "DISCOVERY"
    if preparation_requested:
        return "PREPARATION"
    if weather_requested:
        return "WEATHER"
    return "DISCOVERY" if discovery_requested else "SEARCH"


def build_agent_task_context(
    *,
    task_type: AdvisorTaskType,
    goal: str,
    known_facts: list[str],
    missing_facts: list[str],
    available_tools: list[str] | tuple[str, ...] | set[str],
    weather_requested: bool = False,
) -> AgentTaskContext:
    """Build one validated contract while intersecting tools with task policy."""
    policy_tools, success_criteria, approval_boundary = _TASK_POLICIES[task_type]
    allowed = set(policy_tools) & set(available_tools)
    criteria = list(success_criteria)
    if (
        weather_requested
        and task_type in {"MEAL", "LODGING"}
        and "get_trip_weather" in available_tools
    ):
        # Keep the primary category policy while granting the independently
        # requested, server-scoped forecast capability for this turn.
        allowed.add("get_trip_weather")
        criteria.append("include requested dated weather evidence with source and freshness")
    allowed_tools = sorted(allowed)
    return AgentTaskContext(
        task_type=task_type,
        goal=goal[:1200],
        known_facts=list(dict.fromkeys(known_facts))[:16],
        missing_facts=list(dict.fromkeys(missing_facts))[:16],
        success_criteria=criteria,
        allowed_tools=allowed_tools,
        approval_boundary=approval_boundary,
    )
