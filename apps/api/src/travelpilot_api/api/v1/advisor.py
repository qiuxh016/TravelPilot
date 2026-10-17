import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.trips import find_duplicate_trip, lock_trip_owner
from travelpilot_core.agents.advisor_reply import compose_advisor_reply
from travelpilot_core.agents.extraction import Extraction, extract_places, fallback_extract
from travelpilot_core.agents.runtime import AgentRuntime
from travelpilot_core.engines.optimizer.itinerary import Candidate, Visit, build_itinerary
from travelpilot_core.ports.tool_schemas import GetDistanceMatrixInput, SearchPlacesInput
from travelpilot_core.ports.tools import ToolFailure, ToolSpec
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import (
    MemoryModel,
    UserMemorySettingsModel,
    UserPreferenceMemoryModel,
)
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider, get_weather_provider
from travelpilot_api.rate_limit import enforce_identity_rate_limit, get_rate_limit_redis
from travelpilot_api.schemas.advisor import (
    AdvisorConfirm,
    AdvisorConfirmRead,
    AdvisorDraftCreate,
    AdvisorMessageCreate,
    AdvisorRuntimeStatus,
    AdvisorSessionContextUpdate,
    AdvisorSessionCreate,
    AdvisorSessionRead,
)
from travelpilot_api.settings import settings

router = APIRouter(prefix="/advisor", tags=["advisor"])


@router.get("/status", response_model=AdvisorRuntimeStatus)
async def advisor_runtime_status(_user: UserModel = Depends(get_current_user)):
    configured = bool(settings.llm_api_key.strip() and settings.llm_model.strip())
    return AdvisorRuntimeStatus(
        llm_status="CONFIGURED" if configured else "NOT_CONFIGURED",
        model_name=settings.llm_model.strip() if configured else None,
        output_token_limit=settings.llm_max_tokens_per_turn,
    )


async def _extract_advisor_intent(content: str) -> tuple[Extraction, bool]:
    """Prefer grounded model extraction, but preserve the turn with local fallback."""
    try:
        extraction = await extract_places(
            content,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            token_budget=max(128, settings.llm_max_tokens_per_turn * 2 // 3),
        )
        return extraction, False
    except RuntimeError:
        if not settings.llm_api_key or not settings.llm_model:
            raise
        return fallback_extract(content), True


async def _owned_session(session: AsyncSession, session_id: UUID, owner_id: UUID, *, lock: bool = False):
    statement = select(AdvisorSessionModel).where(
        AdvisorSessionModel.id == session_id,
        AdvisorSessionModel.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise HTTPException(404, detail="顾问会话不存在")
    return row


async def _read_session(session: AsyncSession, row: AdvisorSessionModel) -> AdvisorSessionRead:
    messages = (await session.scalars(select(AdvisorMessageModel).where(
        AdvisorMessageModel.session_id == row.id,
        AdvisorMessageModel.owner_id == row.owner_id,
    ).order_by(AdvisorMessageModel.created_at, AdvisorMessageModel.id))).all()
    context = row.context or {"revision": 0, "selected_candidate_ids": [], "current_plan": None, "confirmed_memories": []}
    draft_stale = bool(row.draft and row.draft.get("context_revision", context.get("revision", 0)) != context.get("revision", 0))
    return AdvisorSessionRead.model_validate({
        **{column.name: getattr(row, column.name) for column in row.__table__.columns},
        "context": context,
        "draft_stale": draft_stale,
        "messages": messages,
    })


async def _refresh_trip_snapshot(db: AsyncSession, row: AdvisorSessionModel) -> dict:
    """Refresh the agent's trip observations before it decides on an action."""
    context = dict(row.context or {})
    if not row.trip_id:
        return context
    trip = await db.scalar(select(TripModel).where(
        TripModel.id == row.trip_id, TripModel.owner_id == row.owner_id,
    ))
    if trip is None:
        return context
    latest_plan = await db.scalar(select(TripPlanModel).where(
        TripPlanModel.trip_id == trip.id,
    ).order_by(TripPlanModel.version.desc()).limit(1))
    plan_rows = (await db.execute(
        select(TripPlanNodeModel, PoiModel)
        .join(PoiModel, PoiModel.id == TripPlanNodeModel.poi_id)
        .where(TripPlanNodeModel.plan_id == latest_plan.id)
        .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
    )).all() if latest_plan else []

    existing_candidates = {
        str(candidate.get("existing_poi_id")): candidate
        for candidate in row.candidates if candidate.get("existing_poi_id")
    }
    plan_candidates = []
    stops = []
    for node, poi in plan_rows:
        previous = existing_candidates.get(str(poi.id), {})
        candidate = {
            **previous,
            "id": previous.get("id") or str(uuid4()),
            "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
            "existing_poi_id": str(poi.id), "in_current_plan": True,
            "name": poi.name, "address": poi.address, "category": poi.category,
            "latitude": float(poi.latitude), "longitude": float(poi.longitude),
            "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
            "confidence": previous.get("confidence", 0.5), "suggestion": previous.get("suggestion"),
        }
        plan_candidates.append(candidate)
        stops.append({
            "candidate_id": candidate["id"], "poi_id": str(poi.id), "name": poi.name,
            "day": node.day.isoformat(), "start_time": node.start_time.isoformat(),
            "end_time": node.end_time.isoformat(), "priority": node.priority,
            "status": node.status, "note": node.note,
        })

    plan_poi_ids = {candidate["existing_poi_id"] for candidate in plan_candidates}
    other_candidates = [
        {**candidate, "in_current_plan": False}
        for candidate in row.candidates
        if not candidate.get("existing_poi_id")
        or str(candidate.get("existing_poi_id")) not in plan_poi_ids
    ]
    row.candidates = [*other_candidates, *plan_candidates]
    selected_ids = [candidate["id"] for candidate in plan_candidates]
    previous_plan = context.get("current_plan") or {}
    if previous_plan.get("version") != (latest_plan.version if latest_plan else None):
        context["revision"] = int(context.get("revision", 0)) + 1
    if set(context.get("selected_candidate_ids", [])) != set(selected_ids):
        context["selected_candidate_ids"] = selected_ids
        context["revision"] = int(context.get("revision", 0)) + 1
    context["current_plan"] = {
        "id": str(latest_plan.id), "version": latest_plan.version,
        "status": latest_plan.status, "warnings": latest_plan.warnings, "stops": stops,
    } if latest_plan else None
    row.destination = trip.destination
    row.start_date = trip.start_date
    row.end_date = trip.end_date
    row.budget = trip.budget
    row.pace_level = trip.pace_level
    current_trip_snapshot = {
        "destination": trip.destination,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "end_date": trip.end_date.isoformat() if trip.end_date else None,
        "budget": str(trip.budget) if trip.budget is not None else None,
        "pace_level": trip.pace_level,
    }
    if context.get("base_trip") != current_trip_snapshot:
        context["base_trip"] = current_trip_snapshot
        context["revision"] = int(context.get("revision", 0)) + 1
    context["planning_state"] = _planning_state(row)
    row.context = context
    return context


def _normalized_place_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())


def _map_popularity_score(metadata: dict | None) -> float | None:
    """Use provider supplied rating/importance without inventing popularity."""
    if not isinstance(metadata, dict):
        return None
    business = metadata.get("biz_ext")
    value = business.get("rating") if isinstance(business, dict) else None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 10:
        return score
    return None


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
            if period in ("下午", "晚上") and 1 <= hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            visit_time = f"{hour:02d}:{int(chinese_time.group(3) or 0):02d}"
    return day, visit_time


def _clock_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _is_schedule_recommendation_request(content: str) -> bool:
    phrases = ("时间怎么安排", "时间如何安排", "安排时间比较", "安排得比较合适", "优化时间", "重新排时间", "排一下时间", "时间分配", "行程怎么排", "路线怎么排")
    return any(phrase in content for phrase in phrases)


def _planning_state(row: AdvisorSessionModel) -> dict:
    existing_trip = row.trip_id is not None
    default_source = "existing_trip" if existing_trip else "session_input"
    sources = (row.context or {}).get("slot_sources", {})
    planning_inputs = (row.context or {}).get("planning_inputs", {})

    def slot(name: str, value):
        source = sources.get(name, default_source)
        missing = value is None or value == "" or value == []
        return {
            "value": value.isoformat() if hasattr(value, "isoformat") else value,
            "source": source if not missing else None,
            "confidence": 1.0 if not missing else 0.0,
            "status": "known" if not missing else "missing",
        }

    slots = {
        "destination": slot("destination", row.destination.strip() or None),
        "start_date": slot("start_date", row.start_date),
        "end_date": slot("end_date", row.end_date),
        "budget": slot("budget", str(row.budget) if row.budget is not None else None),
        "pace_level": slot("pace_level", row.pace_level),
        "preferences": slot("preferences", row.preference_text.strip() or None),
        "origin": slot("origin", planning_inputs.get("origin", "").strip() or None),
        "companions": slot("companions", planning_inputs.get("companions", [])),
        "hard_constraints": slot("hard_constraints", planning_inputs.get("hard_constraints", [])),
    }
    required = ("destination", "start_date", "end_date")
    return {
        "slots": slots,
        "missing_required": [name for name in required if slots[name]["status"] == "missing"],
        "unresolved_questions": [
            {"slot": name, "question": {
                "destination": "你想去哪个城市或区域？",
                "start_date": "旅行从哪一天开始？",
                "end_date": "旅行到哪一天结束？",
            }[name]}
            for name in required if slots[name]["status"] == "missing"
        ],
    }


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
    "自然与公园": ("公园", ("自然", "公园", "户外", "徒步", "山水", "风景")),
    "地方美食": ("美食", ("美食", "吃", "餐厅", "小吃", "火锅", "美食")),
    "历史街区": ("历史古迹", ("历史", "古迹", "古建筑", "老街", "文化")),
    "海滨休闲": ("海滨", ("海边", "海滨", "海岛", "沙滩", "看海")),
}


def _destination_queries(preferences: str, hard_constraints: list[str]) -> list[tuple[str, str]]:
    folded = preferences.casefold()
    excluded = " ".join(hard_constraints).casefold()
    result = []
    for label, (query, terms) in _DESTINATION_INTERESTS.items():
        if any(term in folded for term in terms) and not any(
            marker + term in folded or marker + term in excluded
            for marker in ("不喜欢", "不想", "不要", "不去", "别去", "避免", "不", "别") for term in terms
        ):
            result.append((label, query))
    return result[:2]


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


def _hard_limit_conflicts(candidate: dict, memories: list[dict]) -> list[str]:
    candidate_text = f"{candidate.get('name', '')} {candidate.get('category', '')}".casefold()
    known_categories = {
        "博物馆": ("博物馆", "museum", "展馆", "art gallery"),
        "酒吧与夜店": ("酒吧", "夜店", "bar", "nightclub"),
        "购物": ("购物", "商场", "shopping", "mall"),
        "动物园": ("动物园", "zoo"),
        "宗教场所": ("寺", "教堂", "清真寺", "temple", "church"),
    }
    conflicts = []
    for memory in memories:
        text = str(memory.get("text", ""))
        folded = text.casefold()
        if memory.get("constraint_type") != "HARD_LIMIT":
            continue
        if not any(marker in folded for marker in ("不去", "不喜欢", "不想", "不要", "避开", "禁止", "不能")):
            continue
        for terms in known_categories.values():
            if any(term in folded for term in terms) and any(term.casefold() in candidate_text for term in terms):
                conflicts.append(f"{candidate.get('name')} 与已确认限制“{text}”冲突")
                break
    return conflicts


async def _active_personal_memories(db: AsyncSession, owner_id: UUID) -> list[dict]:
    settings_row = await db.get(UserMemorySettingsModel, owner_id)
    if settings_row is None or not settings_row.enabled:
        return []
    now = datetime.now(UTC)
    memories = (await db.scalars(select(UserPreferenceMemoryModel).where(
        UserPreferenceMemoryModel.owner_id == owner_id,
        UserPreferenceMemoryModel.confirmed.is_(True),
        (UserPreferenceMemoryModel.expires_at.is_(None) | (UserPreferenceMemoryModel.expires_at > now)),
    ).order_by(UserPreferenceMemoryModel.updated_at.desc()).limit(10))).all()
    return [{
        "id": str(memory.id), "category": memory.category, "text": memory.text[:240],
        "constraint_type": memory.constraint_type,
    } for memory in memories]


async def _empty_trace(_: dict) -> None:
    return None


def _search_tool_runtime(maps) -> AgentRuntime:
    async def search(arguments: SearchPlacesInput) -> dict:
        try:
            places = await maps.search_place(arguments.keyword, arguments.city)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        status = (
            "SIMULATED" if any(place.provider == "fake" for place in places)
            else "VERIFIED" if places else "NO_RESULTS"
        )
        return {
            "status": status,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "places": [{
                "provider": place.provider, "provider_id": place.provider_id,
                "name": place.name, "address": place.address, "category": place.category,
                "latitude": place.latitude, "longitude": place.longitude,
                "duration_min": place.suggested_duration_min, "opening_hours": place.opening_hours,
                "popularity_score": _map_popularity_score(place.raw_metadata),
            } for place in places],
        }

    return AgentRuntime(
        [ToolSpec("search_places", SearchPlacesInput, search, timeout_seconds=settings.maps_timeout_seconds,
                  retry_limit=0)],
        _empty_trace,
        max_steps=25,
    )


def _distance_tool_runtime(maps) -> AgentRuntime:
    async def distance_matrix(arguments: GetDistanceMatrixInput) -> dict:
        try:
            durations = await maps.get_distance_matrix(arguments.points)
        except MapsProviderError as exc:
            raise ToolFailure("MAPS_PROVIDER_UNAVAILABLE") from exc
        provider = getattr(maps, "provider", maps)
        provider_name = type(provider).__name__
        simulated = provider_name.casefold().startswith("fake")
        source = "Local simulated maps" if simulated else "AMap Web Service" if provider_name == "AmapProvider" else provider_name
        return {
            "durations_min": durations,
            "status": "SIMULATED" if simulated else "VERIFIED",
            "source": source,
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    return AgentRuntime(
        [ToolSpec("get_distance_matrix", GetDistanceMatrixInput, distance_matrix,
                  timeout_seconds=settings.maps_timeout_seconds, retry_limit=0)],
        _empty_trace,
        max_steps=1,
    )


def _apply_existing_adjustments(
    row: AdvisorSessionModel, content: str, extraction: Extraction | None = None,
) -> tuple[dict, list[str]]:
    context = dict(row.context or {})
    selected = set(context.get("selected_candidate_ids", []))
    original_selected = set(selected)
    messages: list[str] = []
    negative_terms = ("不要", "不去", "不想去", "移除", "删掉", "删除", "去掉", "取消", "排除")
    positive_terms = ("保留", "还要去", "仍想去", "继续去", "安排", "加入", "想去", "要去")
    structured_actions = {
        mention.name: mention.action for mention in (extraction.places if extraction else [])
    }
    for candidate in row.candidates:
        name = candidate["name"]
        position = content.find(name)
        if position < 0:
            continue
        separators = ("，", ",", "。", "；", ";", "但是", "但", "而")
        clause_start = max((content.rfind(separator, 0, position) for separator in separators), default=-1) + 1
        clause_ends = [index for separator in separators if (index := content.find(separator, position)) >= 0]
        clause_end = min(clause_ends, default=len(content))
        nearby_start = max(clause_start, position - 24)
        nearby_end = min(clause_end, position + len(name) + 24)
        nearby = content[nearby_start:nearby_end]
        center = position - nearby_start
        structured_action = structured_actions.get(name)
        negative_found = structured_action == "remove" or any(re.search(term, nearby) for term in negative_terms)
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
        elif intent == "positive" and candidate["id"] not in selected:
            selected.add(candidate["id"])
            messages.append(f"已将{name}加入草案候选")

    lower_pace = ("轻松一点", "轻松一些", "慢一点", "慢一些", "松一点", "多休息", "多留休息", "休息时间", "少安排", "别太赶", "不要太赶", "减少安排")
    higher_pace = ("紧凑一点", "快一点", "多安排", "安排紧凑", "节奏快")
    pace_adjustment = extraction.pace_adjustment if extraction else "none"
    wants_slower = pace_adjustment == "slower" or any(term in content for term in lower_pace)
    wants_faster = pace_adjustment == "faster" or any(term in content for term in higher_pace)
    if wants_slower and row.pace_level > 1:
        row.pace_level -= 1
        messages.append(f"已将节奏调整为 {row.pace_level}/5")
    elif wants_faster and row.pace_level < 5:
        row.pace_level += 1
        messages.append(f"已将节奏调整为 {row.pace_level}/5")

    changed = selected != original_selected or any("节奏调整" in item for item in messages)
    context["selected_candidate_ids"] = sorted(selected)
    if changed:
        context["revision"] = int(context.get("revision", 0)) + 1
    return context, messages


@router.post("/sessions", response_model=AdvisorSessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: AdvisorSessionCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    trip = None
    if payload.trip_id:
        trip = await db.scalar(select(TripModel).where(
            TripModel.id == payload.trip_id, TripModel.owner_id == user.id,
        ))
        if trip is None:
            raise HTTPException(404, detail="旅行不存在")
    personal_memories = await _active_personal_memories(db, user.id)
    row = AdvisorSessionModel(
        owner_id=user.id,
        trip_id=trip.id if trip else None,
        mode=payload.mode,
        destination=trip.destination if trip else payload.destination.strip(),
        start_date=trip.start_date if trip else payload.start_date,
        end_date=trip.end_date if trip else payload.end_date,
        budget=trip.budget if trip else payload.budget,
        pace_level=trip.pace_level if trip else payload.pace_level,
        preference_text=payload.preference_text.strip(),
    )
    db.add(row)
    await db.flush()
    row.context = {
        "revision": 0, "selected_candidate_ids": [], "personal_memories": personal_memories,
        "slot_sources": {
            "origin": "session_input", "companions": "session_input", "hard_constraints": "session_input",
        },
        "planning_inputs": {
            "origin": payload.origin.strip(), "companions": payload.companions,
            "hard_constraints": payload.hard_constraints,
        },
    }
    if trip:
        current_plan = await db.scalar(select(TripPlanModel).where(
            TripPlanModel.trip_id == trip.id,
        ).order_by(TripPlanModel.version.desc()).limit(1))
        existing_places = (await db.execute(
            select(WishlistItemModel, PoiModel)
            .join(PoiModel, PoiModel.id == WishlistItemModel.poi_id)
            .where(WishlistItemModel.trip_id == trip.id)
            .order_by(WishlistItemModel.priority, WishlistItemModel.created_at)
        )).all()
        plan_rows = (await db.execute(
            select(TripPlanNodeModel, PoiModel)
            .join(PoiModel, PoiModel.id == TripPlanNodeModel.poi_id)
            .where(TripPlanNodeModel.plan_id == current_plan.id)
            .order_by(TripPlanNodeModel.day, TripPlanNodeModel.start_time)
        )).all() if current_plan else []
        plan_poi_ids = {poi.id for _, poi in plan_rows}
        candidate_by_poi: dict[UUID, dict] = {}
        row.candidates = []
        for item, poi in existing_places:
            candidate = {
                "id": str(uuid4()), "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
                "existing_poi_id": str(poi.id), "in_current_plan": poi.id in plan_poi_ids,
                "name": poi.name, "address": poi.address, "category": poi.category,
                "latitude": float(poi.latitude), "longitude": float(poi.longitude),
                "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
                "confidence": float(item.interest_score or 0.5), "suggestion": item.notes,
            }
            row.candidates = [*row.candidates, candidate]
            candidate_by_poi[poi.id] = candidate
        for _, poi in plan_rows:
            if poi.id in candidate_by_poi:
                continue
            candidate = {
                "id": str(uuid4()), "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
                "existing_poi_id": str(poi.id), "in_current_plan": True,
                "name": poi.name, "address": poi.address, "category": poi.category,
                "latitude": float(poi.latitude), "longitude": float(poi.longitude),
                "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
                "confidence": 0.5, "suggestion": None,
            }
            row.candidates = [*row.candidates, candidate]
            candidate_by_poi[poi.id] = candidate
        confirmed_memories = (await db.scalars(select(MemoryModel).where(
            MemoryModel.trip_id == trip.id, MemoryModel.confirmed.is_(True),
        ).order_by(MemoryModel.importance.desc(), MemoryModel.created_at.desc()).limit(30))).all()
        plan_stops = [{
            "candidate_id": candidate_by_poi[poi.id]["id"], "poi_id": str(poi.id), "name": poi.name,
            "day": node.day.isoformat(), "start_time": node.start_time.isoformat(),
            "end_time": node.end_time.isoformat(), "priority": node.priority,
            "status": node.status, "note": node.note,
        } for node, poi in plan_rows]
        row.context = {
            "revision": 0,
            "base_pace_level": trip.pace_level,
            "base_trip": {
                "destination": trip.destination, "start_date": trip.start_date.isoformat(),
                "end_date": trip.end_date.isoformat(),
                "budget": str(trip.budget) if trip.budget is not None else None,
                "pace_level": trip.pace_level,
            },
            "selected_candidate_ids": [item["candidate_id"] for item in plan_stops],
            "current_plan": {
                "id": str(current_plan.id), "version": current_plan.version,
                "status": current_plan.status, "warnings": current_plan.warnings,
                "stops": plan_stops,
            } if current_plan else None,
            "confirmed_memories": [{
                "id": str(memory.id), "type": memory.memory_type, "text": memory.text,
                "importance": memory.importance, "confidence": memory.confidence,
            } for memory in confirmed_memories],
            "personal_memories": row.context.get("personal_memories", []),
            "planning_inputs": row.context.get("planning_inputs", {}),
            "slot_sources": row.context.get("slot_sources", {}),
        }
    row.context = {**(row.context or {}), "planning_state": _planning_state(row)}
    welcome = (
        "已载入你自己的旅行上下文。告诉我想保留、增加或调整的地点；我会先核对地点，再生成待确认草案。"
        if trip else
        ("先告诉我偏好的旅行方式；如果还没确定城市和日期，可以稍后在旅行条件中补充。补齐后我会用地图核验候选，再生成待确认草案。"
         if payload.mode == "discover" else
         "会话已创建。告诉我想去的具体地点或偏好；我会用地图服务核验地点。日期、预算和节奏会作为排程约束。")
    )
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant", content=welcome))
    await db.commit()
    await db.refresh(row)
    return await _read_session(db, row)


@router.patch("/sessions/{session_id}/context", response_model=AdvisorSessionRead)
async def update_session_context(
    session_id: UUID,
    payload: AdvisorSessionContextUpdate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    context = dict(row.context or {})
    old_planning_inputs = context.get("planning_inputs", {})
    new_planning_inputs = {
        "origin": payload.origin.strip(), "companions": payload.companions,
        "hard_constraints": payload.hard_constraints,
    }
    before = (row.destination, row.start_date, row.end_date, row.budget, row.pace_level, row.preference_text,
              old_planning_inputs)
    row.destination = payload.destination.strip()
    row.start_date = payload.start_date
    row.end_date = payload.end_date
    row.budget = payload.budget
    row.pace_level = payload.pace_level
    row.preference_text = payload.preference_text.strip()
    after = (row.destination, row.start_date, row.end_date, row.budget, row.pace_level, row.preference_text,
             new_planning_inputs)
    if before != after:
        context["revision"] = int(context.get("revision", 0)) + 1
    context["slot_sources"] = {
        "destination": "advisor_form", "start_date": "advisor_form", "end_date": "advisor_form",
        "budget": "advisor_form", "pace_level": "advisor_form", "preferences": "advisor_form",
        "origin": "advisor_form", "companions": "advisor_form", "hard_constraints": "advisor_form",
    }
    context["planning_inputs"] = new_planning_inputs
    context["planning_state"] = _planning_state(row)
    row.context = context
    row.updated_at = datetime.now(UTC)
    changed_fields = [name for name, old_value, new_value in zip(
        ("destination", "start_date", "end_date", "budget", "pace_level", "preferences", "planning_inputs"),
        before, after, strict=True,
    ) if old_value != new_value]
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="user", operation="update_context",
        status="SUCCEEDED", metadata_json={"changed_fields": changed_fields},
    ))
    db.add(AdvisorMessageModel(
        session_id=row.id, owner_id=user.id, role="assistant",
        content="旅行条件已更新。地点和日期齐全后可以继续搜索候选并生成草案。",
        payload={"event": "CONTEXT_UPDATED", "planning_state": context["planning_state"]},
    ))
    await db.commit()
    await db.refresh(row)
    return await _read_session(db, row)


@router.get("/sessions", response_model=list[AdvisorSessionRead])
async def list_sessions(db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    rows = (await db.scalars(select(AdvisorSessionModel).where(
        AdvisorSessionModel.owner_id == user.id,
    ).order_by(AdvisorSessionModel.updated_at.desc()))).all()
    return [await _read_session(db, row) for row in rows]


@router.get("/sessions/{session_id}", response_model=AdvisorSessionRead)
async def get_session_detail(session_id: UUID, db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    return await _read_session(db, await _owned_session(db, session_id, user.id))


@router.post("/sessions/{session_id}/destinations", response_model=AdvisorSessionRead)
async def recommend_destinations(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    redis: Redis = Depends(get_rate_limit_redis),
):
    """Recommend a few cities using bounded, live POI searches as evidence."""
    started_at = perf_counter()
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    if row.destination:
        raise HTTPException(409, detail="当前会话已有目的地")
    if settings.maps_provider != "amap":
        raise HTTPException(503, detail="目的地推荐需要配置真实地图服务；当前模拟地图不能用于城市推荐")
    planning_inputs = (row.context or {}).get("planning_inputs", {})
    origin = str(planning_inputs.get("origin", "")).strip()
    if not origin:
        raise HTTPException(422, detail="请先填写出发地，再比较目的地")
    if not row.start_date or not row.end_date:
        raise HTTPException(422, detail="请先填写旅行日期，再比较目的地")
    queries = _destination_queries(
        row.preference_text,
        [str(item) for item in planning_inputs.get("hard_constraints", [])],
    )
    if not queries:
        raise HTTPException(422, detail="请先填写至少一项旅行偏好，例如自然、美食、历史街区或海滨休闲")

    await enforce_identity_rate_limit(
        redis,
        str(user.id),
        bucket="advisor-destinations",
        limit=settings.advisor_destination_limit_per_user,
        window_seconds=settings.advisor_destination_window_seconds,
    )

    retrieved_at = datetime.now(UTC).isoformat()
    city_results: dict[str, dict] = {
        city: {"city": city, "matched_interests": [], "places": [], "place_count": 0, "failed_interests": []}
        for city in _DESTINATION_CITIES
    }
    failures = 0
    for city in _DESTINATION_CITIES:
        for label, keyword in queries:
            try:
                places = await maps.search_place(keyword, city)
            except MapsProviderError:
                failures += 1
                city_results[city]["failed_interests"].append(label)
                continue
            verified = [place for place in places if place.provider != "fake"]
            if verified:
                city_results[city]["matched_interests"].append(label)
                city_results[city]["place_count"] += len(verified)
                city_results[city]["places"].extend({
                    "name": place.name,
                    "category": place.category,
                    "address": place.address,
                    "provider": place.provider,
                    "provider_id": place.provider_id,
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                } for place in verified)

    retrieved_at = datetime.now(UTC).isoformat()
    ranked = sorted(
        (item for item in city_results.values() if item["places"]),
        key=lambda item: (-len(item["matched_interests"]), -item["place_count"], item["city"]),
    )[:3]
    for item in ranked:
        unique_places = {place["provider_id"]: place for place in item["places"]}
        item["places"] = list(unique_places.values())[:4]
        item["reasons"] = [f"地图检索到 {item['place_count']} 个相关地点，覆盖 {len(item['matched_interests'])} 类偏好："
                           + "、".join(item["matched_interests"])]
        item["provider"] = item["places"][0]["provider"]
        item["retrieved_at"] = retrieved_at

    if not ranked and failures == len(_DESTINATION_CITIES) * len(queries):
        raise HTTPException(503, detail="地图服务暂时不可用，你的偏好已保留，请稍后重试")
    if ranked:
        reply = "以下城市按地图检索到的偏好相关地点整理。它们是待比较的候选，不代表交通、费用或季节适宜度已核实；选择后仍需确认日期和行程草案。"
        if failures:
            reply += f"另有 {failures} 项地图搜索暂时失败，未用于候选排序。"
    elif failures:
        reply = f"有 {failures} 项地图搜索暂时失败，且当前没有可展示的核验结果。你的偏好已保留，可以稍后重试或直接填写目的地。"
    else:
        reply = "地图服务没有找到可核验的匹配地点。你的偏好已保留，可以修改偏好后重试，或直接填写目的地。"
    db.add(AdvisorMessageModel(
        session_id=row.id,
        owner_id=user.id,
        role="assistant",
        content=reply,
        payload={
            "event": "DESTINATION_RECOMMENDATIONS",
            "recommendations": ranked,
            "source": "AMap Web Service",
            "retrieved_at": retrieved_at,
            "search_count": len(_DESTINATION_CITIES) * len(queries),
            "failed_search_count": failures,
        },
    ))
    db.add(AdvisorRunEventModel(
        owner_id=user.id,
        session_id=row.id,
        phase="tool",
        operation="recommend_destinations",
        status="SUCCEEDED" if ranked else "NO_RESULTS",
        duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        error_code=None,
        metadata_json={"candidate_count": len(ranked), "search_count": len(_DESTINATION_CITIES) * len(queries),
                       "failed_search_count": failures},
    ))
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await _read_session(db, row)


@router.post("/sessions/{session_id}/messages", response_model=AdvisorSessionRead)
async def send_message(
    session_id: UUID,
    payload: AdvisorMessageCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
):
    started_at = perf_counter()
    row = await _owned_session(db, session_id, user.id, lock=True)
    content = payload.content.strip()
    if idempotency_key:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", idempotency_key):
            raise HTTPException(422, detail="Idempotency-Key 格式无效")
        previous = await db.scalar(select(AdvisorMessageModel).where(
            AdvisorMessageModel.session_id == row.id,
            AdvisorMessageModel.owner_id == user.id,
            AdvisorMessageModel.role == "user",
            AdvisorMessageModel.idempotency_key == idempotency_key,
        ))
        if previous is not None:
            if previous.content != content:
                raise HTTPException(409, detail="Idempotency-Key 已用于另一条消息")
            return await _read_session(db, row)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    await _refresh_trip_snapshot(db, row)
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="user", content=content,
                               idempotency_key=idempotency_key))
    try:
        extraction, model_fallback = await _extract_advisor_intent(content)
        allowed_search_queries = _allowed_interest_queries(content, extraction.search_queries)
        stated_interest_queries = list(allowed_search_queries)
        recommendation_requested = extraction.intent == "recommend" or any(
            term in content for term in ("推荐地点", "推荐一些地点", "推荐几个地方", "热门地点", "人气景点", "热度高")
        )
        if recommendation_requested:
            allowed_search_queries = list(dict.fromkeys([*allowed_search_queries, "景点", "美食"]))[:5]
        if (
            stated_interest_queries or extraction.pace_adjustment != "none"
            or any(term in content for term in ("偏好", "喜欢", "不喜欢", "不要", "希望", "不想"))
        ):
            row.preference_text = (row.preference_text + "\n" + content).strip()[-4000:]
        context, adjustments = _apply_existing_adjustments(row, content, extraction)
        found = []
        preselected_candidate_ids = []
        searched_requests = 0
        search_failures = []
        search_runtime = _search_tool_runtime(maps)
        requests = [
            (mention.name, mention.confidence, mention.suggestion, mention.action == "add")
            for mention in extraction.places if mention.action != "remove"
        ]
        requests.extend(
            (query, 0.55, ("按地图服务当前排序检索的热门候选" if recommendation_requested else f"按你提到的“{query}”偏好搜索"), False)
            for query in allowed_search_queries
            if query.casefold() not in {mention.name.casefold() for mention in extraction.places}
        )
        for keyword, confidence, suggestion, select_by_default in (requests if row.destination else []):
            if row.trip_id and any(candidate["name"].casefold() == keyword.casefold() for candidate in row.candidates):
                continue
            searched_requests += 1
            try:
                search_result = await search_runtime.call("search_places", {
                    "keyword": keyword, "city": row.destination or None,
                })
            except ToolFailure as exc:
                search_failures.append(exc.code)
                continue
            for place in search_result["places"][:5]:
                candidate = {
                    "id": str(uuid4()), "provider": place["provider"], "provider_id": place["provider_id"],
                    "name": place["name"], "address": place["address"], "category": place["category"],
                    "latitude": place["latitude"], "longitude": place["longitude"],
                    "duration_min": place["duration_min"], "opening_hours": place["opening_hours"],
                    "confidence": confidence, "suggestion": suggestion,
                    "popularity_score": place.get("popularity_score"),
                    "verification_status": search_result["status"], "verified_at": search_result["retrieved_at"],
                }
                if not any(c["provider"] == candidate["provider"] and c["provider_id"] == candidate["provider_id"] for c in row.candidates):
                    row.candidates = [*row.candidates, candidate]
                    found.append(candidate)
                    if select_by_default:
                        preselected_candidate_ids.append(candidate["id"])
                    if row.trip_id and select_by_default:
                        context["selected_candidate_ids"] = sorted({
                            *context.get("selected_candidate_ids", []), candidate["id"],
                        })
                        context["revision"] = int(context.get("revision", 0)) + 1
        if recommendation_requested and found:
            found.sort(key=lambda item: (item.get("popularity_score") is not None,
                                         item.get("popularity_score") or 0), reverse=True)
            row.candidates = sorted(row.candidates, key=lambda item: (
                item.get("popularity_score") is not None, item.get("popularity_score") or 0
            ), reverse=True)
        schedule_target = None
        if len(extraction.places) == 1:
            mention_name = _normalized_place_name(extraction.places[0].name)
            exact = [candidate for candidate in found if _normalized_place_name(candidate["name"]) == mention_name]
            if len(exact) == 1:
                schedule_target = exact[0]
        elif len(found) == 1:
            schedule_target = found[0]
        if schedule_target is not None:
            explicit_day, explicit_time = _explicit_schedule_hint(content, row.start_date, row.end_date)
            if explicit_day:
                schedule_target["explicit_day"] = explicit_day
            if explicit_time:
                schedule_target["explicit_start_time"] = explicit_time
        schedule_suggestions = []
        schedule_recommendation_requested = bool(
            row.trip_id and (extraction.intent == "schedule" or _is_schedule_recommendation_request(content))
        )
        schedule_recommendation_error = None
        schedule_runtime = None
        if schedule_recommendation_requested:
            current_plan = context.get("current_plan") or {}
            existing_stops = current_plan.get("stops", [])
            plan_poi_ids = {str(stop.get("poi_id")) for stop in existing_stops if stop.get("poi_id")}
            schedule_candidates = [candidate for candidate in row.candidates
                                   if candidate.get("in_current_plan") and str(candidate.get("existing_poi_id")) in plan_poi_ids]
            if not schedule_candidates:
                schedule_recommendation_error = "当前已确认计划中没有可重新排程的地点。"
            elif not row.start_date or not row.end_date:
                schedule_recommendation_error = "当前旅行缺少日期，暂时无法为已确认地点推荐分日安排。"
            elif len(schedule_candidates) > 29:
                schedule_recommendation_error = "地点数量超过路线距离核验上限，请先减少本次排程地点。"
            else:
                planner_candidates = [Candidate(
                    poi_id=UUID(candidate["id"]), name=candidate["name"],
                    latitude=float(candidate["latitude"]), longitude=float(candidate["longitude"]),
                    duration_min=max(15, int(candidate.get("duration_min", 60))), priority=3,
                    interest_score=float(candidate.get("confidence", 0.5)),
                    opening_hours=candidate.get("opening_hours"), note=candidate.get("suggestion"),
                ) for candidate in schedule_candidates]
                recommendation_matrix = None
                try:
                    if len(planner_candidates) > 1:
                        schedule_runtime = _distance_tool_runtime(maps)
                        result = await schedule_runtime.call("get_distance_matrix", {
                            "points": [(candidate.latitude, candidate.longitude) for candidate in planner_candidates],
                        })
                        recommendation_matrix = result["durations_min"]
                    proposed_visits, proposed_warnings = build_itinerary(
                        row.start_date, row.end_date, planner_candidates, recommendation_matrix,
                        buffer_min=90 if row.pace_level <= 2 else 60,
                        transfer_buffer_min=15,
                        max_pois_per_day={1: 3, 2: 4, 3: 5, 4: 6, 5: 6}[row.pace_level],
                    )
                    if len(proposed_visits) != len(planner_candidates):
                        schedule_recommendation_error = "现有地点无法全部放入当前日期和节奏；我没有静默删点，请增加旅行天数或调整节奏后再试。"
                    else:
                        schedule_suggestions = [{
                            "candidate_id": str(visit.candidate.poi_id),
                            "day": visit.day.isoformat(),
                            "start_time": visit.start_time.strftime("%H:%M"),
                        } for visit in proposed_visits]
                        selected_plan_ids = [candidate["id"] for candidate in schedule_candidates]
                        preselected_candidate_ids = selected_plan_ids
                        if set(context.get("selected_candidate_ids", [])) != set(selected_plan_ids):
                            context["selected_candidate_ids"] = selected_plan_ids
                            context["revision"] = int(context.get("revision", 0)) + 1
                except (ToolFailure, MapsProviderError, ValueError) as exc:
                    schedule_recommendation_error = "暂时无法取得可靠的地点间交通时间，未生成自动排程建议。"
        if schedule_suggestions:
            version = current_plan.get("version", "当前")
            reply = f"我读取了当前计划 v{version} 中已确认的 {len(schedule_candidates)} 个地点，并结合地图交通时间和地图建议游览时长排出一版时间建议。它只是待确认草案；检查路线和空档后，点击“确认并保存行程”才会生成新计划。"
        elif found:
            names = "、".join(c["name"] for c in found[:8])
            reply = f"地图服务核对到这些候选：{names}。请先选择想去的地点，并为每个地点确认日期和开始时间。"
            if recommendation_requested:
                reply += "候选按地图服务返回顺序整理；只有地图提供评分时才会按评分辅助排序，不能把它当作全网热度。你更偏爱自然风景、历史文化、美食还是亲子活动？"
            if search_failures:
                reply += " 部分地图查询暂时失败，以上候选仅来自成功的查询。"
        elif not row.destination and row.mode == "discover":
            has_preferences = bool(row.preference_text.strip())
            planning_inputs = (row.context or {}).get("planning_inputs", {})
            missing_city_compare = []
            if not str(planning_inputs.get("origin", "")).strip():
                missing_city_compare.append("出发地")
            if not row.start_date or not row.end_date:
                missing_city_compare.append("旅行日期")
            if not has_preferences:
                reply = "可以从一点偏好开始，不用一次想完整。你更想看自然风景、吃本地美食、逛历史街区，还是去海边放松？也可以直接说预算或同行人。"
            else:
                reply = "我先记下了你的想法。"
                if missing_city_compare:
                    reply += f"要根据地图证据比较城市，还需要{ '和'.join(missing_city_compare) }。"
                reply += "你可以补充这些条件，或者先告诉我想考虑的城市，我就按偏好搜索具体地点。"
        elif not row.destination:
            reply = "我已记下你的想法。先告诉我想去的城市或区域；如果还没确定，可以说说偏好的风景、吃住预算和旅行天数，我会先问最关键的一项，再一起缩小范围。"
        elif schedule_recommendation_requested:
            reply = schedule_recommendation_error or "我读取了当前旅行，但暂时无法生成时间建议；请检查日期和地图路线服务后重试。"
        elif searched_requests and search_failures:
            reply = "地图服务暂时不可用，这轮未能核验地点。你的偏好已保留，可以稍后重试。"
        elif searched_requests:
            reply = "地图服务没有找到匹配候选。可以补充更具体的地点名称或偏好关键词。"
        elif adjustments:
            reply = "已记录你对当前计划的调整。确认变更前会生成新的草案版本，不会修改原计划。"
        else:
            model_note = "已使用配置的模型抽取地点名称" if settings.llm_api_key and settings.llm_model else "当前未配置模型，使用本地规则识别明确提到的地点"
            reply = f"{model_note}。请告诉我具体地点名称；偏好会保存在会话中，但地点需经地图核验后才能进入草案。"
        if adjustments:
            reply += " " + "；".join(adjustments) + "。"
        if row.trip_id and row.context and row.context.get("confirmed_memories") and not adjustments:
            reply += f" 会话已载入 {len(row.context['confirmed_memories'])} 条已确认旅行记忆供你查看。"
        composed, reply_tokens, reply_output_tokens = (None, 0, 0) if model_fallback or not row.destination else await compose_advisor_reply(
            {
                "current_message": content,
                "destination": row.destination or None,
                "dates": {
                    "start": row.start_date.isoformat() if row.start_date else None,
                    "end": row.end_date.isoformat() if row.end_date else None,
                },
                "budget": str(row.budget) if row.budget is not None else None,
                "pace_level": row.pace_level,
                "preferences": row.preference_text[-1200:],
                "confirmed_personal_memories": (row.context or {}).get("personal_memories", []),
                "planning_inputs": (row.context or {}).get("planning_inputs", {}),
                "current_plan": context.get("current_plan"),
                "schedule_recommendation_requested": schedule_recommendation_requested,
                "missing_required": context.get("planning_state", {}).get("missing_required", []),
                "adjustments_applied": adjustments,
                "verified_candidates": [{
                "name": item["name"], "address": item["address"], "category": item["category"],
                "popularity_score": item.get("popularity_score"),
                    "provider": item["provider"], "verification_status": item["verification_status"],
                } for item in found[:8]],
                "search_requests_without_results": searched_requests if not found else 0,
                "search_failures": search_failures,
            },
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            token_budget=settings.llm_max_tokens_per_turn - extraction.output_tokens,
        )
        response_source = "MODEL" if composed else "TEMPLATE"
        if composed:
            reply = composed.reply
            if composed.follow_up_question:
                reply += "\n" + composed.follow_up_question
            if recommendation_requested and not stated_interest_queries and not composed.follow_up_question:
                reply += "\n你更偏爱自然风景、历史文化、美食还是亲子活动？"
        if schedule_suggestions:
            reply = f"我读取了当前计划中已确认的 {len(schedule_suggestions)} 个地点，并结合地图交通时间和地图建议游览时长排出一版时间建议。它只是待确认草案；检查路线和空档后，点击“确认并保存行程”才会生成新计划。"
        elif model_fallback:
            reply += " 模型暂时不可用，本轮使用本地规则识别明确提及的地点；请核对地图候选。"
        llm_output_tokens = extraction.output_tokens + reply_output_tokens
        llm_input_tokens = (
            max(0, extraction.token_usage - extraction.output_tokens)
            + max(0, reply_tokens - reply_output_tokens)
        )
        llm_total_tokens = llm_input_tokens + llm_output_tokens
    except ToolFailure as exc:
        await db.rollback()
        if exc.code == "TOOL_TIMEOUT":
            raise HTTPException(504, detail="地图搜索超时，请稍后重试") from exc
        raise HTTPException(503, detail="地图服务暂时不可用，请稍后重试") from exc
    except MapsProviderError as exc:
        await db.rollback()
        raise HTTPException(503, detail="地图服务暂时不可用，请稍后重试") from exc
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(502, detail="地点信息解析暂时失败，请重试或直接写出地点名称") from exc
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant", content=reply,
                               payload={
                                   "candidate_ids": preselected_candidate_ids,
                                   "intent": extraction.intent,
                                   "agent_action": (
                                       "SCHEDULE_PLAN" if schedule_suggestions else
                                       "SCHEDULE_BLOCKED" if schedule_recommendation_requested else
                                       "RECOMMEND_PLACES" if recommendation_requested and searched_requests else
                                       "SEARCH_PLACES" if searched_requests else
                                       "UPDATE_DRAFT_SELECTION" if adjustments else
                                       "ASK_CLARIFICATION"
                                   ),
                                   "action": "SCHEDULE_PROPOSAL" if schedule_suggestions else None,
                                   "schedule_suggestions": schedule_suggestions,
                                   "response_source": response_source,
                                   "extraction_source": "TEMPLATE" if model_fallback or not settings.llm_api_key or not settings.llm_model else "MODEL",
                                   "model_fallback_reason": "EXTRACTION_FAILED" if model_fallback else None,
                                   "llm_model": settings.llm_model if settings.llm_api_key else None,
                                   "llm_tokens": llm_total_tokens,
                                   "llm_input_tokens": llm_input_tokens,
                                   "llm_output_tokens": llm_output_tokens,
                                   "tool_calls": [{key: call[key] for key in ("tool", "status", "latency_ms", "error_code")}
                                                  for call in [*search_runtime.calls, *(schedule_runtime.calls if schedule_runtime else [])]],
                               }))
    for call in search_runtime.calls:
        db.add(AdvisorRunEventModel(
            owner_id=user.id, session_id=row.id, phase="tool", operation=call["tool"],
            status=call["status"], duration_ms=call["latency_ms"], error_code=call["error_code"],
            metadata_json={"result_count": len((call.get("output_data") or {}).get("places", []))},
        ))
    for call in schedule_runtime.calls if schedule_runtime else []:
        db.add(AdvisorRunEventModel(
            owner_id=user.id, session_id=row.id, phase="tool", operation=call["tool"],
            status=call["status"], duration_ms=call["latency_ms"], error_code=call["error_code"],
            metadata_json={"point_count": len((call.get("input_data") or {}).get("points", []))},
        ))
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="interpretation", operation="send_message",
        status=response_source, duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        token_usage=llm_total_tokens,
        metadata_json={"candidate_count": len(found), "search_count": searched_requests,
                       "failed_search_count": len(search_failures),
                       "model_fallback": model_fallback,
                       "llm_model": settings.llm_model if settings.llm_api_key else None,
                       "llm_input_tokens": llm_input_tokens,
                       "llm_output_tokens": llm_output_tokens},
    ))
    context["planning_state"] = _planning_state(row)
    row.context = context
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await _read_session(db, row)


@router.post("/sessions/{session_id}/draft", response_model=AdvisorSessionRead)
async def create_draft(
    session_id: UUID,
    payload: AdvisorDraftCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    weather=Depends(get_weather_provider),
):
    started_at = perf_counter()
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能重新生成草案")
    if not row.start_date or not row.end_date:
        raise HTTPException(400, detail="请先设置旅行开始和结束日期")
    if not row.destination:
        raise HTTPException(400, detail="请先设置旅行目的地")
    profile_memories = await _active_personal_memories(db, user.id)
    current_context = dict(row.context or {})
    if current_context.get("personal_memories", []) != profile_memories:
        current_context["personal_memories"] = profile_memories
        current_context["revision"] = int(current_context.get("revision", 0)) + 1
        row.context = current_context
    planning_inputs = (row.context or {}).get("planning_inputs", {})
    explicit_limits = [{"constraint_type": "HARD_LIMIT", "text": text}
                       for text in planning_inputs.get("hard_constraints", [])]
    planning_memories = [*profile_memories, *explicit_limits]
    by_id = {candidate["id"]: candidate for candidate in row.candidates}
    selected = [by_id.get(str(candidate_id)) for candidate_id in payload.candidate_ids]
    if any(candidate is None for candidate in selected) or len(set(map(str, payload.candidate_ids))) != len(payload.candidate_ids):
        raise HTTPException(400, detail="候选地点无效或重复")
    hard_conflicts = [message for candidate in selected
                      for message in _hard_limit_conflicts(candidate, planning_memories)]
    if hard_conflicts:
        raise HTTPException(409, detail="；".join(hard_conflicts))
    schedule_by_id = {str(item.candidate_id): item for item in payload.schedules}
    selected_ids = {str(item["id"]) for item in selected}
    if len(schedule_by_id) != len(payload.schedules) or set(schedule_by_id) != selected_ids:
        raise HTTPException(400, detail="每个已选地点都需要且只能设置一个日期和开始时间")
    if any(not row.start_date <= item.day <= row.end_date for item in payload.schedules):
        raise HTTPException(400, detail="地点日期必须在旅行日期范围内")
    planner_candidates = [Candidate(
        poi_id=UUID(item["id"]), name=item["name"], latitude=float(item["latitude"]),
        longitude=float(item["longitude"]), duration_min=max(15, int(item["duration_min"])),
        priority=3, interest_score=float(item.get("confidence", 0.5)),
        opening_hours=item.get("opening_hours"), note=item.get("suggestion"),
    ) for item in selected]
    matrix = None
    start_travel_min = None
    origin_leg = {"status": "NOT_PROVIDED", "from": None, "duration_min": None,
                  "source": None, "retrieved_at": None}
    transport_provenance = {
        "status": "NOT_REQUIRED", "source": None, "retrieved_at": None,
    }
    distance_runtime = None
    origin_query = str(planning_inputs.get("origin", "")).strip()
    origin_warnings = []
    origin_point = None
    origin_match = None
    if origin_query:
        try:
            origin_matches = await maps.search_place(origin_query)
            exact_matches = [place for place in origin_matches
                             if _normalized_place_name(place.name) == _normalized_place_name(origin_query)]
            unique_matches = {(place.latitude, place.longitude): place for place in exact_matches}
            if len(unique_matches) == 1:
                origin_match = next(iter(unique_matches.values()))
                origin_point = (origin_match.latitude, origin_match.longitude)
        except ToolFailure:
            origin_leg = {**origin_leg, "status": "UNAVAILABLE"}
        except MapsProviderError:
            origin_leg = {**origin_leg, "status": "UNAVAILABLE"}
    if origin_point and len(planner_candidates) <= 29:
        try:
            distance_runtime = _distance_tool_runtime(maps)
            result = await distance_runtime.call("get_distance_matrix", {
                "points": [origin_point, *((c.latitude, c.longitude) for c in planner_candidates)],
            })
            full_matrix = result["durations_min"]
            matrix = [row[1:] for row in full_matrix[1:]]
            start_travel_min = full_matrix[0][1:]
            origin_leg = {
                "status": "VERIFIED" if result["status"] == "VERIFIED" else result["status"],
                "from": origin_match.name, "duration_min": min(start_travel_min, default=0),
                "source": result["source"], "retrieved_at": result["retrieved_at"],
            }
            transport_provenance = {key: result[key] for key in ("status", "source", "retrieved_at")}
        except ToolFailure as exc:
            origin_leg = {**origin_leg, "status": "UNAVAILABLE"}
            origin_warnings.append("出发地交通查询超时；本次未使用该估算。"
                                   if exc.code == "TOOL_TIMEOUT"
                                   else "出发地交通查询不可用；本次未使用该估算。")
        except MapsProviderError:
            origin_leg = {**origin_leg, "status": "UNAVAILABLE"}
            origin_warnings.append("出发地交通查询不可用；本次未使用该估算。")
    if origin_query and not origin_point:
        unresolved = origin_leg["status"] != "UNAVAILABLE"
        origin_leg = {
            **origin_leg, "status": origin_leg["status"] if origin_leg["status"] == "UNAVAILABLE" else "UNRESOLVED",
            "from": origin_query,
        }
        origin_warnings.append(
            "未能唯一核验出发地；草案未计入出发地到首个地点的交通时间。"
            if unresolved else "出发地地图服务不可用；草案未计入这段交通时间。"
        )
    elif origin_point and len(planner_candidates) > 29:
        origin_leg = {**origin_leg, "status": "UNAVAILABLE", "from": origin_match.name}
        origin_warnings.append("地点数量达到地图矩阵上限；未计入出发地到首个地点的交通时间。")
    if matrix is None and len(planner_candidates) > 1:
        try:
            distance_runtime = _distance_tool_runtime(maps)
            result = await distance_runtime.call("get_distance_matrix", {
                "points": [(c.latitude, c.longitude) for c in planner_candidates],
            })
            matrix = result["durations_min"]
            transport_provenance = {key: result[key] for key in ("status", "source", "retrieved_at")}
        except ToolFailure as exc:
            if exc.code == "TOOL_TIMEOUT":
                raise HTTPException(504, detail="交通时间查询超时，请稍后重试") from exc
            raise HTTPException(503, detail="交通时间服务暂时不可用") from exc
        except MapsProviderError as exc:
            raise HTTPException(503, detail="交通时间服务暂时不可用") from exc
    max_per_day = {1: 3, 2: 4, 3: 5, 4: 6, 5: 6}[row.pace_level]
    warnings = []
    planner_by_id = {str(item.poi_id): item for item in planner_candidates}
    visits = []
    for candidate_id, schedule in schedule_by_id.items():
        candidate = planner_by_id[candidate_id]
        start_min = _clock_minutes(schedule.start_time)
        end_min = start_min + candidate.duration_min
        if start_min < 6 * 60 or end_min > 23 * 60:
            raise HTTPException(400, detail=f"{candidate.name} 的开始时间或预计游览时长超出 06:00–23:00 可规划范围")
        closing = (candidate.opening_hours or {}).get("close")
        if closing:
            try:
                close_time = time.fromisoformat(str(closing))
                if end_min > _clock_minutes(close_time):
                    warnings.append(f"阻断：{candidate.name} 的预计结束时间晚于地图营业时间")
            except ValueError:
                warnings.append(f"{candidate.name} 的营业时间格式无法核验")
        else:
            warnings.append(f"{candidate.name} 的营业时间没有可核验数据")
        visits.append(Visit(candidate, schedule.day, schedule.start_time,
                            time(end_min // 60, end_min % 60)))
    visits.sort(key=lambda item: (item.day, item.start_time, item.candidate.name))
    daily_counts: dict[date, list[Visit]] = {}
    for visit in visits:
        daily_counts.setdefault(visit.day, []).append(visit)
    for day, day_visits in daily_counts.items():
        if len(day_visits) > max_per_day:
            warnings.append(f"阻断：{day} 有 {len(day_visits)} 个地点，超过当前节奏建议上限 {max_per_day} 个")
        for previous, current in zip(day_visits, day_visits[1:]):
            previous_index = next(i for i, candidate in enumerate(planner_candidates) if candidate.poi_id == previous.candidate.poi_id)
            current_index = next(i for i, candidate in enumerate(planner_candidates) if candidate.poi_id == current.candidate.poi_id)
            travel_min = (matrix[previous_index][current_index] if matrix is not None else
                          max(5, round((((previous.candidate.latitude-current.candidate.latitude)**2 + ((previous.candidate.longitude-current.candidate.longitude)*.86)**2)**.5) * 111 * 2.5)))
            if _clock_minutes(previous.end_time) + travel_min + 15 > _clock_minutes(current.start_time):
                warnings.append(f"阻断：{previous.candidate.name} 到 {current.candidate.name} 的预计游览、交通和 15 分钟换乘缓冲冲突（交通约 {travel_min} 分钟）")
            elif travel_min >= 90:
                warnings.append(f"{previous.candidate.name} 到 {current.candidate.name} 距离较远，地图估算交通约 {travel_min} 分钟，请确认路线是否合适")
        for previous, current in zip(day_visits, day_visits[1:]):
            gap = _clock_minutes(current.start_time) - _clock_minutes(previous.end_time)
            if gap >= 180:
                warnings.append(f"{day} {previous.end_time.strftime('%H:%M')}–{current.start_time.strftime('%H:%M')} 有约 {gap // 60} 小时空档，可安排休息或补充地点")
        meal_terms = ("餐饮", "餐厅", "餐馆", "美食", "小吃", "火锅", "饭店", "快餐", "restaurant", "food")
        lunch_covered = any(11 * 60 <= _clock_minutes(visit.start_time) < 14 * 60
                            and any(term in f"{visit.candidate.name} {visit.candidate.category}".casefold() for term in meal_terms)
                            for visit in day_visits)
        dinner_covered = any(17 * 60 <= _clock_minutes(visit.start_time) < 20 * 60
                             and any(term in f"{visit.candidate.name} {visit.candidate.category}".casefold() for term in meal_terms)
                             for visit in day_visits)
        if not lunch_covered:
            warnings.append(f"{day} 尚未明确安排午餐")
        if not dinner_covered:
            warnings.append(f"{day} 尚未明确安排晚餐")
    scheduled_days = set(daily_counts)
    for offset in range((row.end_date - row.start_date).days + 1):
        trip_day = date.fromordinal(row.start_date.toordinal() + offset)
        if trip_day not in scheduled_days:
            warnings.append(f"{trip_day} 尚无已选安排，整天空档由你决定如何使用")
    category_text = " ".join(f"{item['name']} {item['category']}" for item in selected).casefold()
    if not any(term in category_text for term in ("酒店", "宾馆", "旅馆", "民宿", "住宿", "hotel", "hostel")):
        warnings.append("住宿尚未纳入本次地点草案；请另行确认酒店或民宿")
    if transport_provenance["status"] not in ("VERIFIED", "SIMULATED"):
        warnings.append("地点间交通时间尚未核验")
    warnings.extend(origin_warnings)
    if start_travel_min is not None and visits:
        first_day_visits = [visit for visit in visits if visit.day == row.start_date]
        if first_day_visits:
            first_visit = min(first_day_visits, key=lambda visit: visit.start_time)
            first_index = next(index for index, candidate in enumerate(planner_candidates)
                               if candidate.poi_id == first_visit.candidate.poi_id)
            origin_leg["duration_min"] = start_travel_min[first_index]
    for memory in planning_memories:
        if memory.get("constraint_type") == "HARD_LIMIT" and any(
            term in str(memory.get("text", "")).casefold() for term in ("辣", "过敏", "无障碍", "轮椅", "清真", "素食")
        ):
            warnings.append(f"硬性限制需人工核对：{memory.get('text')}（地图数据无法核验菜单、过敏原或无障碍设施）")
        elif memory.get("constraint_type") == "HARD_LIMIT" and not any(
            term in str(memory.get("text", "")).casefold()
            for term in ("博物馆", "museum", "展馆", "酒吧", "夜店", "bar", "nightclub",
                         "购物", "商场", "shopping", "mall", "动物园", "zoo", "寺", "教堂", "清真寺")
        ):
            warnings.append(f"请人工核对硬性限制：{memory.get('text')}（当前地图和排程数据无法验证该条件）")
    try:
        forecast = await weather.forecast(row.destination, row.start_date, row.end_date)
        weather_summary = {
            **forecast,
            "fetched_at": forecast["fetched_at"].isoformat(),
        }
    except WeatherProviderError:
        weather_summary = {
            "status": "UNAVAILABLE", "city": row.destination, "timezone": None,
            "source": "Open-Meteo", "attribution": "Weather data by Open-Meteo (CC BY 4.0)",
            "fetched_at": datetime.now(UTC).isoformat(), "message": "天气服务暂时不可用。",
            "days": [],
        }
        warnings.append("天气服务暂时不可用；草案未使用天气信息。")
    context = dict(row.context or {})
    selected_ids = [item["id"] for item in selected]
    if row.trip_id and set(context.get("selected_candidate_ids", [])) != set(selected_ids):
        context["selected_candidate_ids"] = selected_ids
        context["revision"] = int(context.get("revision", 0)) + 1
    row.context = context
    selected_by_id = {item["id"]: item for item in selected}
    after_stops = [{
        "candidate_id": str(visit.candidate.poi_id),
        "poi_id": selected_by_id[str(visit.candidate.poi_id)].get("existing_poi_id"),
        "name": visit.candidate.name, "day": visit.day.isoformat(),
        "start_time": visit.start_time.isoformat(), "end_time": visit.end_time.isoformat(),
    } for visit in visits]
    before_stops = (context.get("current_plan") or {}).get("stops", [])
    before_by_key = {item["poi_id"]: item for item in before_stops}
    after_by_key = {
        (stop["poi_id"] or stop["candidate_id"]): stop for stop in after_stops
    }
    changes = []
    for stable_key in sorted(set(before_by_key) | set(after_by_key)):
        before = before_by_key.get(stable_key)
        after = after_by_key.get(stable_key)
        if before is None:
            operation, cost = "ADD", 1
        elif after is None:
            operation, cost = "REMOVE", 3
        elif any(before.get(key) != after.get(key) for key in ("day", "start_time", "end_time")):
            operation, cost = "MOVE", 2
        else:
            operation, cost = "KEEP", 0
        changes.append({
            "stable_key": stable_key, "operation": operation,
            "name": (after or before)["name"], "reason": "按对话与当前偏好重新排程",
            "change_cost": cost, "before": before, "after": after,
        })
    summary = {operation.lower(): sum(change["operation"] == operation for change in changes)
               for operation in ("ADD", "REMOVE", "MOVE", "KEEP")}
    row.draft_version += 1
    row.draft = {
        "version": row.draft_version,
        "context_revision": int(context.get("revision", 0)),
        "generated_at": datetime.now(UTC).isoformat(),
        "selected_candidate_ids": selected_ids,
        "applied_personal_memories": profile_memories,
        "transport": transport_provenance,
        "origin_leg": origin_leg,
        "warnings": warnings,
        "weather": weather_summary,
        "trip_changes": {
            "before": context.get("base_trip", {
                "destination": row.destination, "start_date": row.start_date.isoformat(),
                "end_date": row.end_date.isoformat(), "budget": str(row.budget) if row.budget is not None else None,
                "pace_level": int(context.get("base_pace_level", row.pace_level)),
            }),
            "after": {
                "destination": row.destination, "start_date": row.start_date.isoformat(),
                "end_date": row.end_date.isoformat(), "budget": str(row.budget) if row.budget is not None else None,
                "pace_level": row.pace_level,
            },
        } if row.trip_id else None,
        "pace_change": {
            "before": int(context.get("base_pace_level", row.pace_level)),
            "after": row.pace_level,
        } if int(context.get("base_pace_level", row.pace_level)) != row.pace_level else None,
        "changes": changes,
        "change_summary": summary,
        "stops": [{
            "candidate_id": str(visit.candidate.poi_id), "name": visit.candidate.name,
            "day": visit.day.isoformat(), "start_time": visit.start_time.isoformat(),
            "end_time": visit.end_time.isoformat(), "latitude": visit.candidate.latitude,
            "longitude": visit.candidate.longitude, "category": selected_by_id[str(visit.candidate.poi_id)]["category"],
            "address": selected_by_id[str(visit.candidate.poi_id)]["address"],
            "note": visit.candidate.note,
        } for visit in visits],
    }
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant",
                               content=f"已生成第 {row.draft_version} 版待确认草案，安排 {len(visits)} 个地点。确认前不会写入旅行数据。",
                               payload={
                                   "draft_version": row.draft_version, "warnings": warnings,
                                   "tool_calls": [{key: call[key] for key in ("tool", "status", "latency_ms", "error_code")}
                                                  for call in (distance_runtime.calls if distance_runtime else [])],
                               }))
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="planning", operation="create_draft",
        status="SUCCEEDED", duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        metadata_json={"selected_count": len(selected), "stop_count": len(visits),
                       "warning_count": len(warnings), "weather_status": weather_summary["status"],
                       "transport_status": transport_provenance["status"]},
    ))
    if distance_runtime:
        for call in distance_runtime.calls:
            db.add(AdvisorRunEventModel(
                owner_id=user.id, session_id=row.id, phase="tool", operation=call["tool"],
                status=call["status"], duration_ms=call["latency_ms"], error_code=call["error_code"],
                metadata_json={"point_count": len((call.get("input_data") or {}).get("points", []))},
            ))
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await _read_session(db, row)


@router.post("/sessions/{session_id}/confirm", response_model=AdvisorConfirmRead)
async def confirm_draft(
    session_id: UUID,
    payload: AdvisorConfirm,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status == "CONFIRMED" and row.confirmed_trip_id and row.confirmed_plan_id:
        plan = await db.get(TripPlanModel, row.confirmed_plan_id)
        if plan is None:
            raise HTTPException(409, detail="已关联的行程计划不存在")
        return AdvisorConfirmRead(trip_id=row.confirmed_trip_id, plan_id=plan.id,
                                  plan_version=plan.version, already_confirmed=True)
    if not row.draft or payload.draft_version != row.draft_version:
        raise HTTPException(409, detail="草案版本已变化，请刷新后确认最新版本")
    if not row.draft.get("stops"):
        raise HTTPException(409, detail="草案中没有可保存的已安排地点")
    blockers = [warning for warning in row.draft.get("warnings", []) if str(warning).startswith("阻断：")]
    if blockers:
        raise HTTPException(409, detail="行程存在时间或交通冲突，请调整地点时间后重新生成：" + "；".join(blockers))
    context = row.context or {"revision": 0, "selected_candidate_ids": [], "current_plan": None, "confirmed_memories": []}
    if row.draft.get("context_revision", 0) != context.get("revision", 0):
        raise HTTPException(409, detail="对话偏好或地点清单已变化，请重新生成草案")
    trip = None
    if row.trip_id:
        trip = await db.scalar(select(TripModel).where(TripModel.id == row.trip_id, TripModel.owner_id == user.id))
        if trip is None:
            raise HTTPException(404, detail="旅行不存在")
        latest_plan = await db.scalar(select(TripPlanModel).where(
            TripPlanModel.trip_id == trip.id,
        ).order_by(TripPlanModel.version.desc()).limit(1))
        snapshot_plan = context.get("current_plan")
        latest_snapshot = (str(latest_plan.id), latest_plan.version) if latest_plan else None
        expected_snapshot = (snapshot_plan["id"], snapshot_plan["version"]) if snapshot_plan else None
        if latest_snapshot != expected_snapshot:
            raise HTTPException(409, detail="原旅行计划已在其他位置更新，请重新打开顾问会话")
        base_trip = context.get("base_trip")
        if base_trip and (
            base_trip["destination"] != trip.destination
            or base_trip["start_date"] != trip.start_date.isoformat()
            or base_trip["end_date"] != trip.end_date.isoformat()
            or base_trip["budget"] != (str(trip.budget) if trip.budget is not None else None)
            or base_trip["pace_level"] != trip.pace_level
        ):
            raise HTTPException(409, detail="旅行条件已在其他位置更新，请重新打开顾问会话")
    else:
        if not row.start_date or not row.end_date:
            raise HTTPException(400, detail="行程日期缺失")
        await lock_trip_owner(db, user.id)
        duplicate = await find_duplicate_trip(db, user.id, row.destination, row.start_date, row.end_date)
        if duplicate is not None:
            raise HTTPException(
                409,
                detail="已有一段目的地和日期相同的旅行。请在顶部切换到已有旅行，再关联它继续规划。",
            )
        trip = TripModel(owner_id=user.id, destination=row.destination, start_date=row.start_date,
                         end_date=row.end_date, budget=row.budget, pace_level=row.pace_level, status="DRAFT")
        db.add(trip)
        await db.flush()
    selected_ids = row.draft.get("selected_candidate_ids", [])
    selected = {candidate["id"]: candidate for candidate in row.candidates if candidate["id"] in selected_ids}
    poi_ids: dict[str, UUID] = {}
    for candidate_id in selected_ids:
        candidate = selected.get(candidate_id)
        if candidate is None:
            raise HTTPException(409, detail="草案中的地点候选已失效")
        if candidate.get("existing_poi_id") and trip:
            poi = await db.scalar(select(PoiModel).join(WishlistItemModel, WishlistItemModel.poi_id == PoiModel.id).where(
                PoiModel.id == UUID(candidate["existing_poi_id"]), WishlistItemModel.trip_id == trip.id,
            ))
        else:
            poi = await db.scalar(select(PoiModel).where(PoiModel.provider == candidate["provider"],
                                                         PoiModel.provider_id == candidate["provider_id"]))
        if poi is None:
            poi = PoiModel(name=candidate["name"], category=candidate["category"], address=candidate["address"],
                           latitude=Decimal(str(candidate["latitude"])), longitude=Decimal(str(candidate["longitude"])),
                           suggested_duration_min=candidate["duration_min"], opening_hours=candidate.get("opening_hours"),
                           provider=candidate["provider"], provider_id=candidate["provider_id"])
            db.add(poi)
            await db.flush()
        poi_ids[candidate_id] = poi.id
        existing = await db.scalar(select(WishlistItemModel.id).where(
            WishlistItemModel.trip_id == trip.id, WishlistItemModel.poi_id == poi.id,
        ))
        if existing is None:
            db.add(WishlistItemModel(trip_id=trip.id, poi_id=poi.id, priority=3,
                                     interest_score=Decimal("0.500"), notes=row.preference_text or None,
                                     source_type="ADVISOR"))
    version = (await db.scalar(select(func.max(TripPlanModel.version)).where(TripPlanModel.trip_id == trip.id)) or 0) + 1
    plan = TripPlanModel(trip_id=trip.id, version=version, status="READY", warnings=row.draft.get("warnings", []))
    db.add(plan)
    await db.flush()
    for stop in row.draft.get("stops", []):
        candidate_id = stop["candidate_id"]
        if candidate_id not in poi_ids:
            raise HTTPException(409, detail="草案中的地点候选已失效")
        db.add(TripPlanNodeModel(plan_id=plan.id, poi_id=poi_ids[candidate_id], day=date.fromisoformat(stop["day"]),
                                 start_time=time.fromisoformat(stop["start_time"]),
                                 end_time=time.fromisoformat(stop["end_time"]),
                                 priority=3, status="PLANNED", note=stop.get("note")))
    if row.trip_id:
        for change in row.draft.get("changes", []):
            if change["operation"] == "KEEP":
                continue
            db.add(PlanChangeModel(
                plan_id=plan.id,
                change_type={"ADD": "ADD", "REMOVE": "REMOVE", "MOVE": "UPDATE"}[change["operation"]],
                stable_key=change["stable_key"], reason_code="ADVISOR_EDIT",
                change_cost=change["change_cost"], before_data=change["before"],
                after_data=change["after"], reason=change["reason"],
            ))
        trip.destination = row.destination
        trip.start_date = row.start_date
        trip.end_date = row.end_date
        trip.budget = row.budget
        trip.pace_level = row.pace_level
    row.status = "CONFIRMED"
    row.confirmed_trip_id = trip.id
    row.confirmed_plan_id = plan.id
    row.updated_at = datetime.now(UTC)
    db.add(AdvisorRunEventModel(
        owner_id=user.id, session_id=row.id, phase="user", operation="confirm_draft",
        status="SUCCEEDED", metadata_json={"plan_version": version, "created_trip": row.trip_id is None},
    ))
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant",
                               content="草案已确认并保存到旅行。你可以在行程详情中继续调整。",
                               payload={"trip_id": str(trip.id), "plan_id": str(plan.id)}))
    await db.commit()
    return AdvisorConfirmRead(trip_id=trip.id, plan_id=plan.id, plan_version=version)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: UUID, db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    row = await _owned_session(db, session_id, user.id)
    await db.delete(row)
    await db.commit()
