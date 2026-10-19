"""Small policies that keep Advisor turn composition grounded and predictable."""


_UNSUPPORTED_BOOKING_INTENTS = (
    (
        "LODGING_BOOKING_UNAVAILABLE",
        (
            "订酒店", "订房", "订民宿", "预订酒店", "预定酒店", "预订住宿", "预定住宿",
            "预订民宿", "预定民宿", "预订这家酒店", "预定这家酒店", "预订这家民宿",
            "预定这家民宿", "订这家酒店", "订这家民宿",
        ),
        "当前不能代订酒店或民宿，也不会提交订单。可以继续帮你搜索住宿候选、比较位置，并加入待确认的旅行草案。",
    ),
    (
        "TICKET_BOOKING_UNAVAILABLE",
        ("订机票", "买机票", "订火车票", "买火车票", "订车票", "买车票", "订门票", "买门票", "预订门票", "预定门票"),
        "当前没有接入机票、车票或门票预订服务，不能替你下单。我可以帮你整理出行信息或继续规划旅行安排。",
    ),
    (
        "RESTAURANT_BOOKING_UNAVAILABLE",
        ("订餐位", "订座", "预约座位", "预订餐厅", "预定餐厅"),
        "当前不能代为预约餐厅或保证有位，也不会提交预订。可以帮你搜索餐饮候选并安排一个待确认的用餐时段。",
    ),
)


def unsupported_booking_action(goal: str) -> tuple[str, str] | None:
    """Recognize transactional requests the product cannot currently execute.

    This is a capability boundary, not an attempt to parse general travel intent.
    Match only explicit booking verbs plus a supported travel booking object so a
    normal lodging/meal/ticket recommendation remains eligible for search.
    """
    normalized = " ".join(goal.casefold().split())
    for code, phrases, reply in _UNSUPPORTED_BOOKING_INTENTS:
        if any(phrase in normalized for phrase in phrases):
            return code, reply
    return None


def can_compose_grounded_reply(
    *,
    schedule_recommendation_requested: bool,
    cancelled_pending_task: bool,
    invalidated_pending_task: bool,
    search_loop_question: str | None,
    weather_result: dict | None,
    transport_result,
    transport_route_requested: bool,
    requested_category: str | None,
    preparation_requested: bool,
    verified_candidates_available: bool = False,
    unsupported_booking_requested: bool = False,
) -> bool:
    """Allow read-only natural-language composition unless a scoped flow owns the turn.

    Place extraction failure is deliberately not an exclusion: composing an answer
    from the original request and already-known trip facts is independent of
    extracting candidate places. The composer cannot call tools or persist changes.
    """
    exclusive_flow = any((
        schedule_recommendation_requested,
        cancelled_pending_task,
        invalidated_pending_task,
        search_loop_question is not None,
        weather_result is not None,
        transport_result is not None,
        transport_route_requested,
        unsupported_booking_requested,
    ))
    if verified_candidates_available:
        return not exclusive_flow
    return not any((
        exclusive_flow,
        requested_category is not None,
        preparation_requested,
    ))


def advisor_reply_source(
    *,
    composed_reply: bool,
    model_generated_question: bool,
) -> str:
    """Report who generated the visible reply, not merely the Agent action."""
    return "MODEL" if composed_reply or model_generated_question else "TEMPLATE"


def grounded_general_trip_tip(destination: str | None, place_names: list[str]) -> str | None:
    """Provide a useful, explicitly bounded fallback when model JSON is unusable."""
    names = list(dict.fromkeys(
        name.strip() for name in place_names
        if isinstance(name, str) and name.strip()
    ))[:3]
    if not destination or not names:
        return None
    places = "、".join(names)
    return (
        f"结合你这次{destination}旅行，目前已收藏{places}。一个稳妥的下一步是先核对这些地点的"
        "开放时间和相互交通，再安排每天的停留顺序，并给用餐、转场和休息留出空档；"
        "具体交通和营业信息仍需临近出发时核验。你更想优先减少路上时间，还是尽量都去到？"
    )


def grounded_reply_candidates(
    *,
    current_candidates: list[dict],
    prior_candidates: list[dict],
    searched_requests: int,
    search_failures: list[str],
    requested_category: str | None,
    requested_meal_type: str | None,
) -> list[dict]:
    """Select current or immediately prior evidence without reviving stale results."""
    candidates = current_candidates[:8]
    if not candidates and searched_requests == 0 and not search_failures:
        candidates = prior_candidates[:8]
    if requested_category:
        candidates = [
            item for item in candidates
            if item.get("planning_category") == requested_category
            and (
                requested_meal_type is None
                or item.get("meal_type") == requested_meal_type
            )
        ]
    return candidates
