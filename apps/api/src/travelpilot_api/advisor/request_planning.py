"""Pure message-to-search/task intent planning for one Advisor turn."""

import re
from dataclasses import dataclass
from typing import Any

from travelpilot_core.agents.extraction import Extraction

from travelpilot_api.advisor.interpretation import (
    _allowed_interest_queries,
    _broad_recommendation_queries,
    _explicit_compound_planning_categories,
    _is_schedule_recommendation_request,
    _planning_category_request,
    event_replan_request_type,
)


@dataclass(frozen=True)
class AdvisorRequestPlan:
    """Normalized task scope and bounded map queries derived from one message."""

    compound_search_categories: tuple[str, ...]
    requested_category: str | None
    requested_meal_type: str | None
    allowed_search_queries: tuple[str, ...]
    stated_interest_queries: tuple[str, ...]
    recommendation_requested: bool
    indoor_alternative_requested: bool
    event_replan_type: str | None
    event_replan_handoff: bool
    transport_route_requested: bool
    weather_requested: bool
    preparation_requested: bool
    schedule_requested: bool
    search_requests: tuple[dict[str, Any], ...]


_DESTINATION_CITY_PATTERNS = (
    re.compile(
        r"(?:想去|准备去|计划去|前往|去|到)\s*"
        r"(?P<city>[\u4e00-\u9fff]{2,8}?)(?:市|周末|暑假|寒假|国庆|春节|周边|附近|游玩|旅游|旅行|度假|玩|看看|逛|[，。！？!?；;]|$)"
    ),
    re.compile(
        r"(?:推荐|搜索|搜一下|找一下|找)\s*"
        r"(?:几个|一些|一个|在|去|到)?\s*"
        r"(?P<city>[\u4e00-\u9fff]{2,8}?)(?:市|周边|附近|的(?:景点|美食|餐厅|地方))"
    ),
)
_NEGATED_CITY_PREFIX = re.compile(r"(?:不想|不要|不去|避开|别去|不考虑)[^，。！？!?；;]{0,8}$")
_NON_CITY_MENTIONS = {"别的城市", "其他城市", "这个城市", "那座城市"}
_NON_CITY_TERMS = (
    "几个", "一些", "适合", "值得", "周末", "放松", "地方", "城市",
    "景点", "美食", "别的", "其他", "的",
)
_EXPLICIT_PLACE_RECOMMENDATION = re.compile(
    r"(?:推荐|寻找|找一下|搜索|搜一下|查找)[^，。；！？,;!?]{0,16}"
    r"(?:地点|地方|景点|去处|城市|目的地|游玩|好玩|值得去|热门)"
    r"|(?:有什么|有哪些)[^，。；！？,;!?]{0,12}"
    r"(?:好玩|值得去|景点|地方|去处|城市|目的地|热门地点)"
)
_EXPLICIT_SEARCH_ACTION = re.compile(r"推荐|找|搜索|搜一下|查找|寻找|查询")
_NEGATED_SEARCH_PREFIX = re.compile(r"(?:不想|不要|不用|无需|不需要|别|先不)[^，。；！？,;!?]{0,8}$")
_SCHEDULE_PROPOSAL_EDIT = re.compile(
    r"留空|留出|空出|空档|改成|调整|挪到|排成|重新排|安排时间|分两天|节奏|"
    r"第二天(?:上午|下午|中午)|第一天(?:上午|下午|中午)|减少交通|少走路"
)
_EXPLICIT_DOMAIN_SEARCH = re.compile(
    r"(?:推荐|搜索|搜一下|帮我找|找一下|查询|查找)[^，。；！？,;!?]{0,18}"
    r"(?:餐厅|餐馆|美食|午餐|早餐|晚餐|酒店|民宿|住宿|景点|地点|去处)"
)


def is_schedule_proposal_edit_followup(
    previous_payload: dict[str, Any] | None, current_message: str,
) -> bool:
    """Treat clear time/pace edits after an unconfirmed schedule as schedule work.

    A draft proposal remains the active object until the user confirms it. This
    avoids reinterpreting a meal break or free-time edit as a food search merely
    because the user mentions lunch.
    """
    if not isinstance(previous_payload, dict):
        return False
    suggestions = previous_payload.get("schedule_suggestions")
    task_snapshot = previous_payload.get("agent_task")
    previous_schedule_attempt = bool(
        previous_payload.get("schedule_recommendation_requested") is True
        and isinstance(task_snapshot, dict)
        and task_snapshot.get("task_type") == "SCHEDULE"
        and task_snapshot.get("status") in {"FAILED", "PARTIAL", "WAITING_USER"}
    )
    proposal_exists = (
        isinstance(suggestions, list) and bool(suggestions)
    ) or previous_payload.get("action") == "SCHEDULE_PROPOSAL" or previous_schedule_attempt
    if not proposal_exists or not isinstance(current_message, str):
        return False
    if _EXPLICIT_DOMAIN_SEARCH.search(current_message):
        return False
    return bool(_SCHEDULE_PROPOSAL_EDIT.search(current_message))


def _has_explicit_search_action(text: str) -> bool:
    for match in _EXPLICIT_SEARCH_ACTION.finditer(text):
        prefix = text[max(0, match.start() - 12):match.start()]
        if not _NEGATED_SEARCH_PREFIX.search(prefix):
            return True
    return False


_NEGATED_TASK = re.compile(
    r"(?:不想|不要|不用|不需要|不必|无需|取消|避免|别|先不)"
    r"[^，。；！？,;!?]{0,14}$"
)


def _evidence_grounded_task_kinds(extraction: Extraction, goal: str) -> set[str]:
    """Accept model task routing only when it quotes this turn affirmatively."""
    accepted = set()
    for task in extraction.requested_tasks:
        evidence = task.evidence.strip()
        position = goal.find(evidence)
        if not evidence or position < 0:
            continue
        prefix = goal[max(0, position - 20):position]
        if _NEGATED_TASK.search(prefix):
            continue
        if _NEGATED_TASK.search(evidence):
            continue
        accepted.add(task.kind)
    return accepted


def extract_explicit_destination_city(goal: str) -> str | None:
    """Use only a clearly affirmative, user-named city to scope free exploration."""
    if not isinstance(goal, str) or not goal.strip():
        return None
    for pattern in _DESTINATION_CITY_PATTERNS:
        for match in pattern.finditer(goal):
            city = match.group("city").strip()
            if not city or city in _NON_CITY_MENTIONS or any(term in city for term in _NON_CITY_TERMS):
                continue
            # The intent marker can itself consume part of the negation (for
            # example, ``不想去`` matches starting at ``想去``). Rebuild the
            # marker prefix with its preceding context before checking it.
            marker_prefix = match.group(0)[:match.start("city") - match.start()]
            before_city = goal[max(0, match.start() - 14):match.start()] + marker_prefix
            if _NEGATED_CITY_PREFIX.search(before_city):
                continue
            return city[:80]
    return None


def should_start_destination_recommendation_from_chat(
    *, mode: str | None, trip_id: Any, destination: str | None,
    recommendation_requested: bool, requested_category: str | None,
    compound_search_categories: tuple[str, ...],
) -> bool:
    """Route only unscoped, open-ended discovery to the destination Agent."""
    return bool(
        mode == "discover"
        and not trip_id
        and not destination
        and recommendation_requested
        and requested_category is None
        and not compound_search_categories
    )


def plan_advisor_request(
    *,
    goal: str,
    extraction: Extraction,
    continuing_task: bool,
    continuing_task_type: str | None = None,
    current_message: str | None = None,
    previous_payload: dict[str, Any] | None,
    unsupported_booking: bool,
    transport_route_requested: bool,
    has_trip: bool,
    current_plan_stops: list[dict[str, Any]] | None,
    saved_candidates: list[dict[str, Any]],
) -> AdvisorRequestPlan:
    """Resolve supported user intent without mutating session or trip state."""
    schedule_followup = continuing_task and continuing_task_type == "SCHEDULE"
    scope_text = current_message if schedule_followup and isinstance(current_message, str) else goal
    previous_category = (
        previous_payload.get("planning_category")
        if isinstance(previous_payload, dict) else None
    )
    resume_explicit_compound_domain = bool(
        schedule_followup
        and isinstance(previous_payload, dict)
        and previous_payload.get("schedule_recommendation_requested") is True
        and previous_category in {"FOOD", "LODGING"}
    )
    suppress_context_search = (
        schedule_followup and not _has_explicit_search_action(scope_text)
        and not resume_explicit_compound_domain
    )
    explicit_schedule = bool(
        schedule_followup or _is_schedule_recommendation_request(scope_text)
    )
    explicit_domain_search = bool(_EXPLICIT_DOMAIN_SEARCH.search(scope_text))
    suppress_schedule_side_search = (
        explicit_schedule and not explicit_domain_search
        and not resume_explicit_compound_domain
    )
    task_kinds = (
        set() if suppress_context_search
        else _evidence_grounded_task_kinds(extraction, scope_text)
    )
    detected_compound_categories = (
        () if suppress_context_search else _explicit_compound_planning_categories(scope_text)
    )
    requested_category, meal_type = (
        (None, None) if suppress_context_search else _planning_category_request(scope_text)
    )
    model_categories = tuple(
        category for category, task_kind in (
            ("FOOD", "FOOD_SEARCH"),
            ("LODGING", "LODGING_SEARCH"),
            ("ATTRACTION", "ATTRACTION_SEARCH"),
        ) if task_kind in task_kinds
    )
    if not suppress_context_search and not requested_category and len(model_categories) == 1:
        requested_category = model_categories[0]
    compound_categories = detected_compound_categories or (
        model_categories if len(model_categories) > 1 else ()
    )
    if compound_categories:
        # A multi-domain request belongs to discovery as a whole. Keep its
        # category-specific searches, not the single-category guard.
        requested_category = None
    if suppress_schedule_side_search:
        # Mentioning lunch while allocating time for it is a scheduling
        # constraint, not consent to search for a restaurant.
        compound_categories = ()
        requested_category = None
        meal_type = None
    if (
        continuing_task and not suppress_context_search and not suppress_schedule_side_search
        and isinstance(previous_payload, dict)
    ):
        previous_category = previous_payload.get("planning_category")
        if (
            requested_category is None
            and not compound_categories
            and previous_payload.get("schedule_recommendation_requested") is True
            and previous_category in {"FOOD", "LODGING"}
        ):
            requested_category = previous_category
            previous_meal_type = previous_payload.get("meal_type")
            meal_type = (
                previous_meal_type
                if previous_meal_type in {"BREAKFAST", "LUNCH", "DINNER", "SNACK"}
                else None
            )

    stated_queries = (
        [] if suppress_context_search or suppress_schedule_side_search
        else _allowed_interest_queries(scope_text, extraction.search_queries)
    )
    queries = list(stated_queries)
    recommendation = not suppress_schedule_side_search and (
        bool(compound_categories)
        or requested_category is not None
        or "PLACE_RECOMMENDATION" in task_kinds
        or "ATTRACTION_SEARCH" in task_kinds
        or any(term in scope_text for term in (
            "推荐地点", "推荐一些地点", "推荐几个地方", "热门地点", "人气景点", "热度高",
        ))
        or bool(_EXPLICIT_PLACE_RECOMMENDATION.search(scope_text))
    )
    if unsupported_booking:
        recommendation = False
    indoor_alternative = any(
        term in scope_text for term in ("室内替代", "室内备选", "下雨备选", "雨天备选")
    )
    if suppress_schedule_side_search:
        queries = []
    elif indoor_alternative:
        recommendation = True
        queries = ["室内景点"]
    if compound_categories:
        category_queries = {"FOOD": "餐饮服务", "LODGING": "住宿服务", "ATTRACTION": "景点"}
        queries = [category_queries[item] for item in compound_categories]
    elif requested_category == "FOOD":
        queries = ["餐饮服务"]
    elif requested_category == "LODGING":
        queries = ["住宿服务"]
    elif "ATTRACTION_SEARCH" in task_kinds and not compound_categories:
        queries = ["景点"]
    elif recommendation and not indoor_alternative:
        queries = _broad_recommendation_queries(scope_text, queries)

    event_type = (
        event_replan_request_type(goal)
        if has_trip and current_plan_stops else None
    )
    event_handoff = bool(has_trip and current_plan_stops and event_type)

    requests = [
        {
            "keyword": mention.name,
            "confidence": mention.confidence,
            "suggestion": mention.suggestion,
            "select_by_default": mention.action == "add",
            # A named place in the user's message is an explicit lookup target.
            "required": True,
        }
        for mention in extraction.places
        if mention.action != "remove"
    ]
    requests.extend(
        {
            "keyword": query,
            "confidence": 0.55,
            "suggestion": (
                f"按你同时提出的需求搜索{query}候选"
                if compound_categories
                else "按地图服务当前排序检索的热门候选"
                if recommendation
                else f"按你提到的“{query}”偏好搜索"
            ),
            "select_by_default": False,
        }
        for query in queries
        if query.casefold() not in {mention.name.casefold() for mention in extraction.places}
    )
    if transport_route_requested:
        requests = []
    if compound_categories:
        category_queries = {"FOOD": "餐饮服务", "LODGING": "住宿服务", "ATTRACTION": "景点"}
        requests = [
            {
                "keyword": category_queries[item],
                "confidence": 0.8,
                "suggestion": f"按你同时提出的需求搜索{category_queries[item]}候选",
                "select_by_default": False,
            }
            for item in compound_categories
        ]
    elif requested_category:
        food_request = requested_category == "FOOD"
        requests = [{
            "keyword": "餐饮服务" if food_request else "住宿服务",
            "confidence": 0.8,
            "suggestion": "按你的用餐需求搜索餐饮地点" if food_request else "按你的住宿需求搜索住宿地点",
            "select_by_default": False,
        }]
    if unsupported_booking:
        requests = []
        transport_route_requested = False
    if has_trip:
        saved_names = {
            str(candidate.get("name", "")).casefold()
            for candidate in saved_candidates
            if isinstance(candidate, dict)
        }
        requests = [request for request in requests if request["keyword"].casefold() not in saved_names]

    # Only require queries grounded in explicit user intent. Broad fallback
    # queries for an open-ended recommendation remain optional so the Agent
    # can ask a useful preference question before exploring.
    required_queries = {item.casefold() for item in stated_queries}
    if compound_categories:
        required_queries.update({
            "FOOD": "餐饮服务", "LODGING": "住宿服务", "ATTRACTION": "景点",
        }[item].casefold() for item in compound_categories)
    elif requested_category == "FOOD":
        required_queries.add("餐饮服务")
    elif requested_category == "LODGING":
        required_queries.add("住宿服务")
    if indoor_alternative:
        required_queries.add("室内景点")
    requests = [
        {**request, "required": request.get("required") is True
         or request["keyword"].casefold() in required_queries}
        for request in requests
    ]

    return AdvisorRequestPlan(
        compound_search_categories=compound_categories,
        requested_category=requested_category,
        requested_meal_type=meal_type,
        allowed_search_queries=tuple(queries),
        stated_interest_queries=tuple(stated_queries),
        recommendation_requested=recommendation,
        indoor_alternative_requested=indoor_alternative,
        event_replan_type=event_type,
        event_replan_handoff=event_handoff,
        transport_route_requested=transport_route_requested or (
            "ROUTE_QUERY" in task_kinds and not unsupported_booking
        ),
        weather_requested=(
            "WEATHER_CHECK" in task_kinds or "PREPARATION_ADVICE" in task_kinds
        ),
        preparation_requested="PREPARATION_ADVICE" in task_kinds,
        schedule_requested="SCHEDULE" in task_kinds,
        search_requests=tuple(requests),
    )
