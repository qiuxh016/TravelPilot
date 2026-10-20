"""Natural-language interpretation and preference helpers for Advisor."""

import re
from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.extraction import (
    Extraction,
    PlaceMention,
    extract_places,
    fallback_extract,
)
from travelpilot_infra.db.models.advisor import AdvisorSessionModel
from travelpilot_infra.db.models.workflow import UserMemorySettingsModel, UserPreferenceMemoryModel

from travelpilot_api.advisor.policies import _excluded_category
from travelpilot_api.settings import settings


async def _extract_advisor_intent(
    content: str, *, token_budget: int | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    personal_memories: list[dict] | None = None,
) -> tuple[Extraction, bool]:
    """Prefer grounded model extraction, but preserve the turn with local fallback."""
    try:
        extraction = await extract_places(
            content,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            token_budget=max(
                128,
                min(
                    settings.llm_max_tokens_per_turn * 2 // 3,
                    token_budget if isinstance(token_budget, int) and not isinstance(token_budget, bool)
                    else settings.llm_max_tokens_per_turn * 2 // 3,
                ),
            ),
            thinking_mode="disabled" if urlsplit(settings.llm_base_url).hostname == "api.deepseek.com" else None,
            conversation_context=conversation_context,
            travel_preferences=[
                memory["text"][:160]
                for memory in (personal_memories or [])
                if isinstance(memory, dict)
                and memory.get("constraint_type") == "PREFERENCE"
                and isinstance(memory.get("text"), str)
                and memory["text"].strip()
            ][:5],
            additional_instructions=(
                " confirmed_travel_preferences contains only previously confirmed soft preferences. "
                "For broad recommendation requests only, you may add up to two matching search_queries. "
                "This is the sole exception to requiring an explicitly named search category. "
                "Do not use these preferences when the current_message names a different category or rejects one; "
                "never create place mentions, actions, pace changes, dates, schedules, or approvals from memory."
            ),
        )
        return extraction, False
    except RuntimeError:
        if not settings.llm_api_key or not settings.llm_model:
            raise
        return fallback_extract(content), True


def _normalized_place_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())


def _resolve_explicit_candidate_reference(
    content: str,
    candidates: list[dict],
    conversation_context: list[dict[str, str]] | None,
) -> tuple[str, str, str] | None:
    """Resolve an explicit ordinal only against candidates named in this conversation."""
    reference = re.search(
        r"第\s*(一|二|三|1|2|3)\s*(?!天|日|周|次)(?:个|处)?\s*(?:地点|地方|景点)?",
        content,
    )
    if not reference:
        return None
    ordinal = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}[reference.group(1)]
    negative_terms = ("移除", "删掉", "删除", "去掉", "取消", "排除", "不去", "不想去")
    positive_terms = ("加入", "添加", "增加", "安排", "保留", "想去", "要去")
    negative = any(term in content for term in negative_terms)
    negative = negative or bool(re.search(
        r"不要\s*(?:去\s*)?第\s*(?:一|二|三|1|2|3)\s*(?:个|处)\s*(?:地点|地方|景点)",
        content,
    ))
    positive = any(term in content for term in positive_terms)
    if negative == positive:
        return None
    action = "remove" if negative else "add"
    # Do not interpret a negated operation such as “不要移除第一个地点” as removal.
    if re.search(r"(?:不要|別|别|不用)\s*(?:把)?[^。；;]{0,12}(?:移除|删掉|删除|去掉|取消|排除)", content):
        return None

    names = [item.get("name") for item in candidates if isinstance(item.get("name"), str)]
    for message in reversed(conversation_context or []):
        if message.get("role") not in {"user", "assistant"}:
            continue
        text = message.get("content", "")
        matches = sorted(
            ((text.find(name), -len(name), name) for name in names if name and name in text),
        )
        ordered_names = list(dict.fromkeys(name for _, _, name in matches))
        if ordinal <= len(ordered_names):
            return ordered_names[ordinal - 1], action, reference.group(0)
    return None


def _resolve_all_candidate_references(
    content: str,
    candidates: list[dict],
    conversation_context: list[dict[str, str]] | None,
) -> set[str]:
    """Resolve an explicit all/count reference to places named by the user.

    The reference is accepted only when its stated count matches the distinct
    trip candidates in a prior user message. Assistant suggestions alone never
    grant selection authority.
    """
    count_match = re.search(
        r"(?P<count>[一二两三四五六七八九十\d]+)\s*(?:个|处|家)?\s*"
        r"(?:地点|地方|景点|候选)?\s*(?:都|全部)?\s*(?:安排|排|加入|保留)",
        content,
    )
    all_reference = any(term in content for term in ("这些都安排", "这些全部安排", "全部收藏地点", "所有收藏地点"))
    if not count_match and not all_reference:
        return set()

    count = None
    if count_match:
        raw = count_match.group("count")
        chinese_counts = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        count = chinese_counts.get(raw)
        if count is None:
            try:
                count = int(raw)
            except ValueError:
                count = None
    candidate_names = {
        str(item.get("name", "")).strip(): item
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item.get("name", "").strip()
    }
    for message in reversed(conversation_context or []):
        if message.get("role") != "user":
            continue
        text = message.get("content", "")
        mentions = sorted(
            ((text.find(name), name) for name in candidate_names if name in text),
        )
        names = list(dict.fromkeys(name for _, name in mentions))
        if names and (all_reference and count is None or len(names) == count):
            return set(names)
    return set()


def _explicit_schedule_hint(content: str, start_date: date | None, end_date: date | None) -> tuple[str | None, str | None]:
    """Extract only dates/times stated explicitly; never infer a default visit time."""
    day = None
    iso_date = re.search(r"(?<!\d)(20\d{2}-\d{1,2}-\d{1,2})(?!\d)", content)
    month_day = re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]", content)
    day_index = re.search(r"第\s*(\d{1,2})\s*天", content)
    if iso_date:
        try:
            parsed = date.fromisoformat(iso_date.group(1))
            if (not start_date or parsed >= start_date) and (not end_date or parsed <= end_date):
                day = parsed.isoformat()
        except ValueError:
            pass
    elif month_day and start_date:
        try:
            parsed = date(int(month_day.group(1) or start_date.year), int(month_day.group(2)), int(month_day.group(3)))
            if not end_date or start_date <= parsed <= end_date:
                day = parsed.isoformat()
        except ValueError:
            pass
    elif day_index and start_date:
        offset = int(day_index.group(1)) - 1
        candidate_day = date.fromordinal(start_date.toordinal() + offset)
        if end_date and candidate_day <= end_date:
            day = candidate_day.isoformat()
    time_match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", content)
    visit_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else None
    if visit_time is None:
        chinese_time = re.search(r"(上午|早上|中午|下午|晚上)\s*(\d{1,2})\s*(?:点|时)(?:\s*(\d{1,2})\s*分?)?", content)
        if chinese_time:
            hour = int(chinese_time.group(2))
            period = chinese_time.group(1)
            if (
                period in ("下午", "晚上") and 1 <= hour < 12
                or period == "中午" and hour < 11
            ):
                hour += 12
            visit_time = f"{hour:02d}:{int(chinese_time.group(3) or 0):02d}"
    return day, visit_time


def _clock_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _is_schedule_recommendation_request(content: str) -> bool:
    normalized = content.casefold()
    phrases = (
        "时间怎么安排", "时间如何安排", "安排时间比较", "安排得比较合适",
        "优化时间", "重新排时间", "排一下时间", "时间分配", "行程怎么排",
        "路线怎么排", "安排行程", "规划行程", "调整行程", "优化行程",
        "安排景点", "规划景点", "排一下景点", "安排游览", "规划游览",
        "整天行程", "全天行程", "一日游", "两日游", "多日游",
        "生成完整计划", "直接生成计划", "生成完整行程", "直接生成完整计划",
        "帮我生成计划", "帮我生成行程", "完整的计划",
    )
    if any(_has_unnegated_schedule_phrase(normalized, phrase) for phrase in phrases):
        return True

    # Catch ordinary formulations such as "把收藏地点安排成两日行程";
    # fixed phrase checks alone miss a verb separated from 行程 by duration words.
    schedule_pattern = re.compile(
        r"(?:安排|规划|排).{0,12}(?:行程|[一二两三四五六七八九十\d]+日(?:游|行程)?|"
        r"[一二三四五六七八九十\d]+天(?:游|行程)?)"
    )
    for match in schedule_pattern.finditer(normalized):
        prefix = normalized[max(0, match.start() - 10):match.start()]
        if not _negated_schedule_prefix(prefix):
            return True

    # Natural wording can put the duration before the scheduling verb:
    # "分两天排". Keep this separate from meal-only wording such as
    # "午餐时间怎么安排".
    distribute_pattern = re.compile(
        r"分\s*(?:[一二两三四五六七八九十\d]+)\s*(?:天|日)\s*(?:来)?\s*(?:安排|排)"
    )
    if any(
        not _negated_schedule_prefix(normalized[max(0, match.start() - 10):match.start()])
        for match in distribute_pattern.finditer(normalized)
    ):
        return True

    return is_fatigue_replan_request(content)


def _has_unnegated_schedule_phrase(content: str, phrase: str) -> bool:
    for match in re.finditer(re.escape(phrase), content):
        prefix = content[max(0, match.start() - 10):match.start()]
        if _negated_schedule_prefix(prefix):
            continue
        return True
    return False


def should_use_deterministic_schedule_extraction(
    *, content: str, has_trip: bool, saved_candidates: list[dict], output_token_budget: int,
) -> bool:
    """Ground explicit scheduling of named saved stops in the user's current text.

    This narrow path only extracts explicit names and actions from the current
    message; referential edits still use model extraction.
    """
    if (
        not has_trip
        or not isinstance(output_token_budget, int)
        or isinstance(output_token_budget, bool)
        or not _is_schedule_recommendation_request(content)
        or not isinstance(saved_candidates, list)
    ):
        return False
    saved_names = [
        item.get("name") for item in saved_candidates
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
    ]
    folded = content.casefold()
    if not any(name.casefold() in folded for name in saved_names):
        return False
    return not bool(re.search(r"第\s*[一二三四五六七八九十\d]+\s*(?:个|处)?", content))


def _negated_schedule_prefix(prefix: str) -> bool:
    return bool(re.search(
        r"(?:不想|不要|不用|不需要|无需|别|先不)(?:再|去|给我)?"
        r"(?:安排|规划|排(?:一下)?|游览)?\s*$",
        prefix,
    ))


def is_fatigue_replan_request(content: str) -> bool:
    """Identify an explicit request to adapt a current itinerary to fatigue."""
    normalized = content.casefold()
    fatigue = ("累", "疲劳", "走不动", "体力不支")
    current_plan_scope = ("今天", "现在", "后面", "接下来", "下午", "当前", "这段行程")
    adjustment = ("调整", "改一下", "改得", "少安排", "减少", "放松", "放慢", "休息", "提前结束")
    return (
        any(term in normalized for term in fatigue)
        and any(term in normalized for term in current_plan_scope)
        and any(term in normalized for term in adjustment)
    )


def event_replan_request_type(content: str) -> str | None:
    """Map explicit in-trip changes to the existing, confirmation-gated event flow."""
    normalized = content.casefold()
    if is_fatigue_replan_request(content):
        return "USER_FATIGUE"

    asks_to_adjust = any(term in normalized for term in (
        "调整", "重新安排", "重排", "改一下", "换一下", "后续行程", "接下来的安排", "安排到后面",
    ))
    if not asks_to_adjust:
        return None

    if any(term in normalized for term in (
        "下雨了", "暴雨", "大雨", "下雪", "暴雪", "大风", "天气突变", "天气变化",
        "高温", "降温", "雨雪",
    )):
        return "WEATHER_CHANGE"
    if any(term in normalized for term in ("延误", "堵车", "晚了", "迟到", "耽搁")):
        return "TIME_DELAY"
    if any(term in normalized for term in (
        "临时想去", "临时加", "临时加入", "临时想加", "加入行程", "加到行程", "安排到后面",
    )):
        return "USER_ADD"
    if any(term in normalized for term in (
        "闭馆", "关门了", "临时关闭", "去不了", "无法入场", "取消预约", "地点取消",
    )):
        return "POI_CANCELLED"
    if any(term in normalized for term in ("预约改", "预约变", "预约调整", "预约时间变")):
        return "RESERVATION_CHANGE"
    return None


def event_replan_prefill(content: str, event_type: str | None) -> dict[str, str | int]:
    """Carry only explicit, bounded event facts to the user's confirmation form."""
    normalized = content.casefold()
    if event_type == "WEATHER_CHANGE":
        conditions = (
            ("STORM", ("暴风雨", "雷暴", "台风")),
            ("SNOW", ("下雪", "暴雪")),
            ("RAIN", ("下雨", "暴雨", "大雨")),
            ("WIND", ("大风", "刮风")),
            ("HEAT", ("高温", "太热", "热浪")),
            ("COLD", ("降温", "变冷", "太冷")),
        )
        for condition, phrases in conditions:
            if any(phrase in normalized for phrase in phrases):
                return {"condition": condition}
    if event_type == "TIME_DELAY":
        if "半小时" in normalized:
            return {"delay_min": 30}
        if "一小时" in normalized or "1小时" in normalized:
            return {"delay_min": 60}
        match = re.search(r"(?<!\d)(\d{1,3})\s*(?:分钟|min)", normalized)
        if match:
            minutes = int(match.group(1))
            if 1 <= minutes <= 180:
                return {"delay_min": minutes}
    return {}


def _planning_category_request(content: str) -> tuple[str | None, str | None]:
    """Identify explicit food or lodging planning so results stay in that domain."""
    text = content.casefold()
    negation = re.compile(r"(?:不想|不要|不用|不去|不喜欢|避免|别去|不看|不需要|不安排|先不)[^，。；！？,;!?]{0,6}$")

    def has_positive_term(terms: tuple[str, ...]) -> bool:
        for term in terms:
            start = 0
            while (index := text.find(term, start)) >= 0:
                prefix = text[max(0, index - 12):index]
                if not negation.search(prefix):
                    return True
                start = index + len(term)
        return False

    meal_type = next((
        kind for kind, terms in (
            ("BREAKFAST", ("早餐", "早饭")),
            ("LUNCH", ("午餐", "午饭", "中饭")),
            ("DINNER", ("晚餐", "晚饭")),
            ("SNACK", ("夜宵", "小吃", "下午茶")),
        ) if has_positive_term(terms)
    ), None)
    food_terms = ("美食", "吃饭", "吃什么", "餐厅", "餐馆", "饭店", "用餐", "火锅", "小吃", "咖啡", "早餐", "午餐", "午饭", "中饭", "晚餐", "晚饭", "夜宵", "下午茶")
    lodging_terms = ("酒店", "住宿", "民宿", "住哪里", "住哪儿", "旅馆")
    if meal_type or has_positive_term(food_terms):
        return "FOOD", meal_type
    if has_positive_term(lodging_terms):
        return "LODGING", None
    return None, None


def _explicit_compound_planning_categories(content: str) -> tuple[str, ...]:
    """Return multiple explicitly requested discovery domains, never inferred ones."""
    text = content.casefold()
    categories = []

    def has_positive_term(terms: tuple[str, ...]) -> bool:
        negation = re.compile(r"(?:不想|不要|不用|不去|不喜欢|避免|别去|不看|不需要|不安排|先不)[^，。；！？,;!?]{0,6}$")
        for term in terms:
            start = 0
            while (index := text.find(term, start)) >= 0:
                if not negation.search(text[max(0, index - 12):index]):
                    return True
                start = index + len(term)
        return False

    if has_positive_term((
        "美食", "餐厅", "餐馆", "饭店", "用餐", "吃饭", "吃什么", "早餐", "午餐", "午饭",
        "晚餐", "晚饭", "夜宵", "火锅", "川菜", "小吃", "咖啡",
    )):
        categories.append("FOOD")
    if has_positive_term(("酒店", "住宿", "民宿", "旅馆", "住哪里", "住哪儿")):
        categories.append("LODGING")
    if has_positive_term((
        "景点", "游玩", "游览", "博物馆", "古镇", "公园", "打卡", "观光", "逛逛",
    )):
        categories.append("ATTRACTION")
    return tuple(categories) if len(categories) > 1 else ()


def _candidate_planning_category(category: str, name: str = "") -> str:
    """Map provider category/name into the product's planning domains."""
    text = f"{category} {name}".casefold()
    if any(term in text for term in ("餐饮", "餐厅", "餐馆", "饭店", "小吃", "咖啡", "food", "restaurant", "美食")):
        return "FOOD"
    if any(term in text for term in ("住宿", "酒店", "宾馆", "民宿", "旅馆", "hotel", "lodging")):
        return "LODGING"
    return "ATTRACTION"




_INTEREST_TERMS = {
    "美食": ("美食", "吃", "餐厅", "餐馆", "火锅", "川菜", "小吃", "咖啡"),
    "博物馆": ("博物馆", "展馆", "艺术馆"),
    "公园": ("公园", "绿地", "自然风景"),
    "购物": ("购物", "商场", "买东西"),
    "夜生活": ("夜生活", "酒吧", "夜店"),
    "历史景点": ("历史", "古迹", "古建筑", "遗址"),
}

# This bounded seed list limits provider calls. Recommendations are ranked only
# from live map evidence; the list itself is not presented as evidence.
_DESTINATION_CITIES = ("成都", "杭州", "西安", "厦门", "青岛", "长沙")
_DESTINATION_INTERESTS = {
    "亲子活动": ("亲子", ("亲子", "带孩子", "带娃", "儿童", "家庭游", "小朋友")),
    "博物馆与展览": ("博物馆", ("博物馆", "展览", "美术馆", "科技馆")),
    "徒步与户外": ("徒步", ("徒步", "登山", "露营", "骑行", "户外活动")),
    "摄影与观景": ("观景台", ("摄影", "拍照", "日落", "观景", "看夜景")),
    "温泉与休闲": ("温泉", ("温泉", "泡汤", "休闲度假", "度假放松")),
    "购物与街区": ("购物", ("购物", "商场", "买东西", "逛街")),
    "自然与公园": ("公园", ("自然", "公园", "户外", "山水", "风景", "森林", "湖泊")),
    "地方美食": ("美食", ("美食", "吃", "餐厅", "小吃", "火锅", "美食")),
    "历史街区": ("历史古迹", ("历史", "古迹", "古建筑", "老街", "文化")),
    "海滨休闲": ("海滨", ("海边", "海滨", "海岛", "沙滩", "看海")),
}


def _destination_queries(preferences: str, hard_constraints: list[str]) -> list[tuple[str, str]]:
    folded = preferences.casefold()
    excluded = " ".join(hard_constraints).casefold()
    result = []
    for label, (query, terms) in _DESTINATION_INTERESTS.items():
        if (
            any(term in folded for term in terms)
            and not _excluded_category(folded, terms)
            and not _excluded_category(excluded, terms)
        ):
            result.append((label, query))
    # Keep the user's stated interest order when several categories compete for
    # the bounded query budget. Static taxonomy order could otherwise discard
    # an explicitly named later interest (for example, beach before scenery).
    interest_positions = {
        label: min(
            (
                position
                for term in terms
                if (position := folded.find(term.casefold())) >= 0
            ),
            default=len(folded),
        )
        for label, (_query, terms) in _DESTINATION_INTERESTS.items()
    }
    result.sort(key=lambda item: interest_positions[item[0]])
    return result[:2]


def _prioritized_destination_queries(
    current_request: str,
    contextual_preferences: str,
    hard_constraints: list[str],
) -> list[tuple[str, str]]:
    """Keep the current request ahead of stored preferences without bypassing exclusions."""
    combined = "\n".join(part for part in (current_request, contextual_preferences) if part)
    current = _destination_queries(current_request, hard_constraints)
    if current and re.search(r"(?:只想|只要|只看|只去|只安排|仅想|仅看|仅限)", current_request):
        return current[:2]
    all_supported = _destination_queries(combined, hard_constraints)
    return list(dict.fromkeys([*current, *all_supported]))[:2]


def _allowed_interest_queries(content: str, queries: list[str]) -> list[str]:
    """Drop model-suggested search categories contradicted by explicit user intent."""
    positive = content.casefold()
    rejected = set()
    for category, terms in _INTEREST_TERMS.items():
        if any(term in positive for term in terms) and any(
            marker + term in positive
            for marker in ("不想", "不要", "不喜欢", "不爱", "避开", "不去")
            for term in terms
        ):
            rejected.add(category)
    result = []
    for query in queries:
        query = query.strip()[:100]
        if not query:
            continue
        if any(query == category and category in rejected for category in rejected):
            continue
        if any(query in terms for category, terms in _INTEREST_TERMS.items() if category in rejected):
            continue
        result.append(query)
    return list(dict.fromkeys(result))


def _broad_recommendation_queries(content: str, queries: list[str]) -> list[str]:
    """Add only broad domains the current user message has not rejected."""
    text = content.casefold()
    negations = ("不想", "不要", "不喜欢", "不爱", "避开", "不去", "不看", "别去")
    domains = {
        "景点": ("景点", "游玩", "游览", "观光"),
        "美食": (
            "美食", "吃饭", "用餐", "餐饮", "吃什么", "早餐", "早饭", "午餐",
            "午饭", "中饭", "晚餐", "晚饭", "夜宵", "下午茶",
        ),
    }
    rejected = set()
    for label, terms in domains.items():
        for term in terms:
            start = 0
            while (position := text.find(term, start)) >= 0:
                prefix = text[max(0, position - 12):position]
                if any(marker in prefix for marker in negations):
                    rejected.add(label)
                    break
                start = position + len(term)
    result = [query for query in queries if query not in rejected]
    if "景点" not in rejected and "景点" not in result:
        result.append("景点")
    if "美食" not in rejected and "美食" not in result:
        result.append("美食")
    return result[:5]


async def _active_personal_memories(db: AsyncSession, owner_id: UUID) -> list[dict]:
    settings_row = await db.get(UserMemorySettingsModel, owner_id)
    if settings_row is None or not settings_row.enabled:
        return []
    now = datetime.now(UTC)
    active_filters = (
        UserPreferenceMemoryModel.owner_id == owner_id,
        UserPreferenceMemoryModel.confirmed.is_(True),
        (UserPreferenceMemoryModel.expires_at.is_(None) | (UserPreferenceMemoryModel.expires_at > now)),
    )
    hard_limits = (await db.scalars(select(UserPreferenceMemoryModel).where(
        *active_filters,
        UserPreferenceMemoryModel.constraint_type == "HARD_LIMIT",
    ).order_by(UserPreferenceMemoryModel.updated_at.desc()))).all()
    preferences = (await db.scalars(select(UserPreferenceMemoryModel).where(
        *active_filters,
        UserPreferenceMemoryModel.constraint_type == "PREFERENCE",
    ).order_by(UserPreferenceMemoryModel.updated_at.desc()).limit(5))).all()
    return [{
        "id": str(memory.id), "category": memory.category, "text": memory.text[:240],
        "constraint_type": memory.constraint_type,
    } for memory in [*hard_limits, *preferences]]


def _apply_existing_adjustments(
    row: AdvisorSessionModel, content: str, extraction: Extraction | None = None,
    conversation_context: list[dict[str, str]] | None = None,
    *, allow_pace_adjustment: bool = True,
) -> tuple[dict, list[str]]:
    context = dict(row.context or {})
    selected = set(context.get("selected_candidate_ids", []))
    original_selected = set(selected)
    messages: list[str] = []
    negative_terms = (
        "不要去", "不要安排", "不安排", "不需要", "不想去", "不去",
        "移除", "删掉", "删除", "去掉", "取消", "排除",
    )
    positive_terms = ("保留", "还要去", "仍想去", "继续去", "安排", "加入", "想去", "要去")
    structured_actions = {
        mention.name: mention.action for mention in (extraction.places if extraction else [])
    }
    structured_mentions = {
        mention.name: mention for mention in (extraction.places if extraction else [])
    }
    reference = _resolve_explicit_candidate_reference(
        content, row.candidates, conversation_context,
    )
    all_referenced_names = _resolve_all_candidate_references(
        content, row.candidates, conversation_context,
    )
    for name in all_referenced_names:
        structured_actions[name] = "add"
    resolved_reference_name = reference[0] if reference else None
    if reference:
        name, action, evidence = reference
        structured_actions[name] = action
        structured_mentions[name] = PlaceMention(
            name=name, confidence=1, action=action, evidence=evidence,
        )
    for candidate in row.candidates:
        name = candidate["name"]
        position = content.find(name)
        mention = structured_mentions.get(name)
        structured_action = structured_actions.get(name)
        referenced_all = name in all_referenced_names
        referenced_action = position < 0 and (
            structured_action in {"add", "remove"} or referenced_all
        )
        if position < 0:
            evidence = getattr(mention, "evidence", None)
            if referenced_all:
                nearby, center = content, 0
            else:
                if not referenced_action or not isinstance(evidence, str) or evidence not in content:
                    continue
                nearby = evidence
                center = 0
            # An extractor must not remove a candidate the user never named.
            # Ordinal/pronoun removals are accepted only through the separate
            # deterministic resolver above, which ties them to conversation context.
            if structured_action == "remove" and resolved_reference_name != name:
                continue
        else:
            separators = ("，", ",", "。", "；", ";", "但是", "但", "而")
            clause_start = max((content.rfind(separator, 0, position) for separator in separators), default=-1) + 1
            clause_ends = [index for separator in separators if (index := content.find(separator, position)) >= 0]
            clause_end = min(clause_ends, default=len(content))
            nearby_start = max(clause_start, position - 24)
            nearby_end = min(clause_end, position + len(name) + 24)
            nearby = content[nearby_start:nearby_end]
            center = position - nearby_start
        candidate_prefix = nearby[:center]
        direct_exclusion = bool(re.search(
            r"(?:不要|不想|不去|不喜欢|不爱|避免|别去|别选|先不)\s*(?:去|安排|选择)?\s*$",
            candidate_prefix,
        ))
        negated_removal = bool(re.search(
            r"(?:不要|别|不想|不需要)\s*(?:移除|删除|删掉|去掉|取消|排除)\s*$",
            candidate_prefix,
        ))
        negative_found = direct_exclusion or (
            not negated_removal and (
                resolved_reference_name == name and reference[1] == "remove"
                or any(re.search(term, nearby) for term in negative_terms)
            )
        )
        intents = [
            (abs((match.start() + len(term)) - center), "positive")
            for term in positive_terms
            for match in re.finditer(term, nearby)
        ]
        intent = "negative" if negative_found else (
            "positive" if structured_action == "add" else
            min(intents, default=(0, "none"), key=lambda item: item[0])[1]
        )
        if intent == "negative":
            if candidate["id"] in selected:
                selected.remove(candidate["id"])
                messages.append(f"已从草案移除{name}")
        elif (
            intent == "positive"
            or (
                _is_schedule_recommendation_request(content)
                and position >= 0
            )
            or name in all_referenced_names
        ) and candidate["id"] not in selected:
            selected.add(candidate["id"])
            messages.append(f"已将{name}加入草案候选")

    lower_pace = (
        "轻松一点", "轻松一些", "轻松点", "慢一点", "慢一些", "松一点", "放松些",
        "多休息", "多留休息", "休息时间", "少安排", "少走点", "别太赶", "不要太赶",
        "减少安排", "减少后续安排",
    )
    higher_pace = ("紧凑一点", "快一点", "多安排", "安排紧凑", "节奏快")
    pace_adjustment = extraction.pace_adjustment if extraction else "none"
    fatigue_adjustment = _is_schedule_recommendation_request(content) and any(
        term in content.casefold() for term in ("累", "疲劳", "走不动", "体力不支")
    )
    wants_slower = (
        pace_adjustment == "slower"
        or any(term in content for term in lower_pace)
        or fatigue_adjustment
    )
    wants_faster = pace_adjustment == "faster" or any(term in content for term in higher_pace)
    if allow_pace_adjustment and wants_slower and row.pace_level > 1:
        row.pace_level -= 1
        messages.append(f"已将节奏调整为 {row.pace_level}/5")
    elif allow_pace_adjustment and wants_faster and row.pace_level < 5:
        row.pace_level += 1
        messages.append(f"已将节奏调整为 {row.pace_level}/5")

    changed = selected != original_selected or any("节奏调整" in item for item in messages)
    context["selected_candidate_ids"] = sorted(selected)
    if changed:
        context["revision"] = int(context.get("revision", 0)) + 1
    return context, messages
