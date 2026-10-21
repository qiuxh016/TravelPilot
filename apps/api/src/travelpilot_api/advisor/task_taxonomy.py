"""Small, explicit taxonomy for user-visible Advisor task state."""

import re
from typing import Literal

AdvisorTaskType = Literal[
    "DISCOVERY", "SCHEDULE", "SEARCH", "MEAL", "LODGING", "TRANSPORT",
    "WEATHER", "PREPARATION", "MEMORY", "GENERAL",
]

ADVISOR_TASK_TYPES: frozenset[str] = frozenset({
    "DISCOVERY", "SCHEDULE", "SEARCH", "MEAL", "LODGING", "TRANSPORT",
    "WEATHER", "PREPARATION", "MEMORY", "GENERAL",
})

_TRANSPORT_TERMS = ("交通", "怎么去", "怎么到", "路线", "打车", "公交", "地铁", "火车", "航班")
_PREPARATION_TERMS = (
    "带什么", "带伞", "穿什么", "穿衣", "衣服怎么准备", "行李", "准备什么", "出行准备",
)
_MEMORY_TERMS = ("旅行记忆", "旅行记录", "游记", "照片文案", "回忆", "记录这次旅行")
_ROUTE_QUERY_TERMS = (
    "怎么走", "怎么去", "怎么坐", "怎么乘", "路线", "交通时间",
    "两地之间", "开车多久", "驾车路线", "步行", "走路", "徒步",
    "开车到", "驾车到", "自驾到",
)
_UNSUPPORTED_ROUTE_MODE_TERMS = ("火车", "高铁", "飞机", "铁路", "动车", "航班")


def is_route_query_request(goal: str) -> bool:
    return any(term in goal.casefold() for term in _ROUTE_QUERY_TERMS)


def is_itinerary_route_validation_request(goal: str) -> bool:
    """Recognize whole-itinerary validation separately from a point-to-point query."""
    normalized = goal.casefold().strip()
    if "当前行程中已保存地点之间的路线" in normalized:
        return True
    asks_to_validate = any(term in normalized for term in ("校验", "核验", "检查", "验证"))
    refers_to_itinerary = any(
        term in normalized
        for term in ("行程", "整段", "全部地点", "所有地点", "已保存地点", "地点之间")
    )
    has_route = any(term in normalized for term in ("路线", "路程", "交通时间"))
    if asks_to_validate and refers_to_itinerary and has_route:
        return True
    # A short mode-qualified action also means the current itinerary. Explicit
    # endpoint requests such as "核验 A 到 B 的路线" stay point-to-point.
    has_explicit_pair = bool(re.search(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,40}\s*(?:到|至)\s*[\u4e00-\u9fffA-Za-z0-9]{2,40}",
        normalized,
    ))
    return (
        asks_to_validate and has_route and not has_explicit_pair
        and any(term in normalized for term in ("校验路线", "核验路线", "检查路线", "验证路线"))
    )


def is_preparation_request(goal: str) -> bool:
    return any(term in goal.casefold() for term in _PREPARATION_TERMS)


def requests_unsupported_route_mode(goal: str) -> bool:
    normalized = goal.casefold()
    return any(term in normalized for term in _UNSUPPORTED_ROUTE_MODE_TERMS)


def unsupported_route_mode_question() -> str:
    """Disclose missing intercity transport without silently changing mode."""
    return (
        "跨城铁路和航班查询尚未接入，我无法核验班次、时刻或票价。"
        "如果你想查询行程中两处已保存地点之间的路线，请告诉我起点、终点和出行方式；"
        "我不会自动改成驾车路线。"
    )


def accepts_driving_route_confirmation(question: str | None, answer: str) -> bool:
    """Accept an affirmative answer only when the pending prompt offered driving."""
    if not question or "驾车路线" not in question:
        return False
    normalized = answer.strip().casefold()
    if not normalized or any(term in normalized for term in ("不", "不要", "不用", "算了", "取消")):
        return False
    return any(term in normalized for term in ("可以", "好的", "好", "行", "没问题", "开车", "驾车", "自驾"))


def infer_task_type(
    *,
    goal: str,
    schedule_requested: bool,
    planning_category: str | None,
    weather_requested: bool,
    discovery_requested: bool,
    search_requested: bool = False,
) -> AdvisorTaskType:
    """Classify one primary outcome; typed food/lodging requests stay scoped."""
    if planning_category == "FOOD":
        return "MEAL"
    if planning_category == "LODGING":
        return "LODGING"
    if schedule_requested:
        return "SCHEDULE"
    normalized = goal.casefold()
    if any(term in normalized for term in _PREPARATION_TERMS):
        return "PREPARATION"
    if any(term in normalized for term in _TRANSPORT_TERMS) or is_route_query_request(goal):
        return "TRANSPORT"
    if any(term in normalized for term in _MEMORY_TERMS):
        return "MEMORY"
    if discovery_requested:
        return "DISCOVERY"
    if search_requested:
        return "SEARCH"
    if weather_requested:
        return "WEATHER"
    return "GENERAL"
