"""Owner-scoped trip observations and planning state for Advisor turns."""

import hashlib
import json
from datetime import date
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import AdvisorSessionModel
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel

from travelpilot_api.advisor.candidate_identity import provider_candidate_id
from travelpilot_api.advisor.draft_service import overlaps_time_window
from travelpilot_api.advisor.interpretation import _candidate_planning_category
from travelpilot_api.advisor.route_evidence import reusable_route_evidence


def _selected_candidates_after_trip_refresh(
    context: dict,
    *,
    plan_id: str | None,
    plan_version: int | None,
    plan_candidate_ids: list[str],
    available_candidate_ids: set[str],
    retained_candidate_ids: list[str] | None = None,
) -> list[str]:
    """Keep the user's draft selection while the underlying plan is unchanged."""
    previous_plan = context.get("current_plan") or {}
    same_plan = (
        previous_plan.get("id") == plan_id
        and previous_plan.get("version") == plan_version
    )
    if same_plan and "selected_candidate_ids" in context:
        selected = [
            candidate_id for candidate_id in context.get("selected_candidate_ids", [])
            if candidate_id in available_candidate_ids
        ]
    else:
        selected = list(dict.fromkeys([
            *plan_candidate_ids,
            *(candidate_id for candidate_id in retained_candidate_ids or []
              if candidate_id in available_candidate_ids),
        ]))
    if set(context.get("selected_candidate_ids", [])) != set(selected):
        context["selected_candidate_ids"] = selected
        context["revision"] = int(context.get("revision", 0)) + 1
    return selected


def planning_state(row: AdvisorSessionModel) -> dict:
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


def sync_personal_memory_snapshot(context: dict, memories: list[dict]) -> tuple[dict, bool]:
    """Refresh bounded prompt memories and invalidate context on any memory change.

    Keep all active hard limits in the separate server-side fingerprint, while
    exposing at most five hard limits and five soft preferences to the LLM.
    """
    updated = dict(context)
    hard_limits = [
        dict(memory) for memory in memories
        if isinstance(memory, dict) and memory.get("constraint_type") == "HARD_LIMIT"
    ]
    preferences = [
        dict(memory) for memory in memories
        if isinstance(memory, dict) and memory.get("constraint_type") == "PREFERENCE"
    ]
    visible = hard_limits[:5] + preferences[:5]
    fingerprint = profile_memory_fingerprint(memories)
    old_memories = updated.get("personal_memories", [])
    old_fingerprint = updated.get(
        "personal_memory_fingerprint", profile_memory_fingerprint(old_memories),
    )
    if old_memories == visible and old_fingerprint == fingerprint:
        return updated, False
    updated["personal_memories"] = visible
    updated["personal_memory_fingerprint"] = fingerprint
    updated["revision"] = int(updated.get("revision", 0)) + 1
    return updated, True


def profile_memory_fingerprint(memories: list[dict]) -> str:
    """Hash the complete active profile-memory set for stale-draft checks."""
    records = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        expiry = memory.get("expires_at")
        records.append({
            "id": str(memory.get("id", "")),
            "category": str(memory.get("category", "")),
            "text": str(memory.get("text", "")),
            "constraint_type": str(memory.get("constraint_type", "")),
            "expires_at": expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry or ""),
        })
    encoded = json.dumps(sorted(records, key=lambda item: item["id"]), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def refresh_trip_snapshot(db: AsyncSession, row: AdvisorSessionModel) -> dict:
    """Refresh trip, current plan, and saved place observations under owner scope."""
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
            "id": previous.get("id") or provider_candidate_id(
                session_id=row.id,
                provider=poi.provider or "local",
                provider_id=poi.provider_id or str(poi.id),
            ),
            "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
            "existing_poi_id": str(poi.id), "in_current_plan": True,
            "name": poi.name, "address": poi.address, "category": poi.category,
            "planning_category": previous.get("planning_category") or _candidate_planning_category(poi.category, poi.name),
            "meal_type": previous.get("meal_type"),
            "latitude": float(poi.latitude), "longitude": float(poi.longitude),
            "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
            "environment_profile": poi.environment_profile,
            "plan_status": node.status,
            "fixed_schedule": ({
                "day": node.day.isoformat(), "start_time": node.start_time.strftime("%H:%M"),
            } if str(node.status).upper() in {"FIXED", "RESERVED"} else None),
            "confidence": previous.get("confidence", 0.5), "suggestion": previous.get("suggestion"),
        }
        plan_candidates.append(candidate)
        stops.append({
            "candidate_id": candidate["id"], "poi_id": str(poi.id), "name": poi.name,
            "category": poi.category,
            "planning_category": previous.get("planning_category") or _candidate_planning_category(poi.category, poi.name),
            "day": node.day.isoformat(), "start_time": node.start_time.isoformat(),
            "end_time": node.end_time.isoformat(), "priority": node.priority,
            "status": node.status, "note": node.note,
        })

    plan_poi_ids = {candidate["existing_poi_id"] for candidate in plan_candidates}
    other_candidates = [
        {**candidate, "in_current_plan": False, "plan_status": "PLANNED", "fixed_schedule": None}
        for candidate in row.candidates
        if not candidate.get("existing_poi_id")
        or str(candidate.get("existing_poi_id")) not in plan_poi_ids
    ]
    wishlist_rows = (await db.execute(
        select(WishlistItemModel, PoiModel)
        .join(PoiModel, PoiModel.id == WishlistItemModel.poi_id)
        .where(WishlistItemModel.trip_id == trip.id)
        .order_by(WishlistItemModel.priority, WishlistItemModel.created_at)
    )).all()
    wishlist_candidates_by_poi = {
        str(candidate.get("existing_poi_id")): candidate for candidate in other_candidates
    }
    for item, poi in wishlist_rows:
        poi_key = str(poi.id)
        if poi_key in plan_poi_ids:
            continue
        previous = wishlist_candidates_by_poi.get(poi_key, {})
        wishlist_candidates_by_poi[poi_key] = {
            **previous,
            "id": previous.get("id") or provider_candidate_id(
                session_id=row.id,
                provider=poi.provider or "local",
                provider_id=poi.provider_id or str(poi.id),
            ),
            "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
            "existing_poi_id": poi_key, "in_current_plan": False,
            "name": poi.name, "address": poi.address, "category": poi.category,
            "planning_category": previous.get("planning_category") or _candidate_planning_category(poi.category, poi.name),
            "meal_type": previous.get("meal_type"),
            "latitude": float(poi.latitude), "longitude": float(poi.longitude),
            "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
            "environment_profile": item.environment_profile or poi.environment_profile,
            "confidence": float(item.interest_score or 0.5), "suggestion": item.notes,
        }
    previous_selected = set(context.get("selected_candidate_ids", []))
    retained_lodging_ids = [
        str(candidate.get("id")) for candidate in row.candidates
        if candidate.get("id") in previous_selected
        and candidate.get("planning_category") == "LODGING"
    ]
    row.candidates = [*wishlist_candidates_by_poi.values(), *plan_candidates]
    selected_ids = [candidate["id"] for candidate in plan_candidates]
    previous_plan = context.get("current_plan") or {}
    if previous_plan.get("version") != (latest_plan.version if latest_plan else None):
        context["revision"] = int(context.get("revision", 0)) + 1
    _selected_candidates_after_trip_refresh(
        context,
        plan_id=str(latest_plan.id) if latest_plan else None,
        plan_version=latest_plan.version if latest_plan else None,
        plan_candidate_ids=selected_ids,
        available_candidate_ids={candidate["id"] for candidate in row.candidates},
        retained_candidate_ids=retained_lodging_ids,
    )
    context["current_plan"] = {
        "id": str(latest_plan.id), "version": latest_plan.version,
        "status": latest_plan.status, "warnings": latest_plan.warnings, "stops": stops,
    } if latest_plan else None
    row.destination = trip.destination
    row.start_date = trip.start_date
    row.end_date = trip.end_date
    row.budget = trip.budget
    previous_trip_snapshot = context.get("base_trip") or {}
    # Keep an Advisor session's pending pace edits across turns. If the trip's
    # persisted pace changed outside this session, adopt that newer value.
    if previous_trip_snapshot.get("pace_level") != trip.pace_level:
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
    context["planning_state"] = planning_state(row)
    row.context = context
    return context


def trip_completeness_audit(row: AdvisorSessionModel, context: dict | None = None) -> list[str]:
    """Summarize visible trip gaps from saved plan and candidates without guessing provider facts."""
    context = context or row.context or {}
    plan = context.get("current_plan") or {}
    stops = plan.get("stops", [])
    audit: list[str] = [] if stops else ["还没有按日期安排游览或用餐地点"]
    days: dict[str, list[dict]] = {}
    for stop in stops:
        days.setdefault(str(stop.get("day", "")), []).append(stop)
    for day, day_stops in sorted(days.items()):
        normalized = sorted(day_stops, key=lambda stop: str(stop.get("start_time", "")))
        for meal_type, start, end, label in (("LUNCH", 11 * 60, 14 * 60, "午餐"), ("DINNER", 17 * 60, 20 * 60, "晚餐")):
            covered = False
            for stop in normalized:
                try:
                    start_hour, start_minute = map(
                        int, str(stop.get("start_time", "00:00"))[:5].split(":"),
                    )
                    stop_start = start_hour * 60 + start_minute
                except (TypeError, ValueError):
                    continue
                text = f"{stop.get('name', '')} {stop.get('category', '')}".casefold()
                is_food = (
                    stop.get("planning_category") == "FOOD"
                    or any(term in text for term in ("餐", "饭", "美食", "小吃", "火锅", "food", "restaurant"))
                )
                if not is_food:
                    continue
                try:
                    end_hour, end_minute = map(
                        int, str(stop.get("end_time", ""))[:5].split(":"),
                    )
                    stop_end = end_hour * 60 + end_minute
                except (TypeError, ValueError):
                    # Older plans may not have an end time. Preserve their
                    # previous start-time interpretation instead of inventing
                    # a duration that could hide an unplanned meal.
                    stop_end = stop_start
                if overlaps_time_window(stop_start, stop_end, start, end):
                    covered = True
                    break
            if not covered:
                audit.append(f"{day} 尚未明确安排{label}")
        for previous, current in pairwise(normalized):
            try:
                prev_end = str(previous.get("end_time", "00:00"))[:5]
                next_start = str(current.get("start_time", "00:00"))[:5]
                ph, pm = map(int, prev_end.split(":"))
                nh, nm = map(int, next_start.split(":"))
                gap = (nh * 60 + nm) - (ph * 60 + pm)
            except (TypeError, ValueError):
                continue
            if gap >= 180:
                audit.append(f"{day} {prev_end}–{next_start} 有约 {gap // 60} 小时空档")

    trip_days = (row.end_date - row.start_date).days + 1 if row.start_date and row.end_date else 1
    if row.start_date and row.end_date:
        for offset in range(trip_days):
            day = date.fromordinal(row.start_date.toordinal() + offset).isoformat()
            if day not in days:
                audit.append(f"{day} 目前没有任何已安排地点")
    candidates = row.candidates or []
    if trip_days > 1 and not any(candidate.get("planning_category") == "LODGING" for candidate in candidates):
        audit.append("尚未添加住宿备选；如已自行安排或不需要住宿，可以忽略")
    planning_inputs = context.get("planning_inputs", {})
    if not str(planning_inputs.get("origin", "")).strip():
        audit.append("没有出发地信息，无法估算出发地到首个地点的交通时间")
    route_evidence = context.get("agent_route_evidence") or {}
    if len(stops) > 1:
        candidate_ids = [str(stop.get("candidate_id", "")) for stop in stops]
        reusable = reusable_route_evidence(
            route_evidence,
            candidate_ids,
            int(context.get("revision", 0)),
        )
        if reusable is None or reusable.get("status") != "VERIFIED":
            audit.append("地点之间的实际交通路线尚未核验")
    draft = getattr(row, "draft", None)
    if isinstance(draft, dict) and draft.get("context_revision") == context.get("revision"):
        for impact in (draft.get("weather") or {}).get("plan_impacts", []):
            if isinstance(impact, dict) and isinstance(impact.get("day"), str) and isinstance(impact.get("name"), str):
                audit.append(f"{impact['day']} 天气提醒：{impact['name']} 可能受降水影响")
    return audit[:12]


def trip_action_items(row: AdvisorSessionModel, context: dict | None = None) -> list[dict[str, str]]:
    """Turn deterministic completeness findings into safe, user-invoked next steps."""
    items: list[dict[str, str]] = []
    missing_meals: dict[str, list[str]] = {"午餐": [], "晚餐": []}
    for finding in trip_completeness_audit(row, context):
        if finding.startswith("还没有按日期安排"):
            items.append({
                "key": "PLAN:EMPTY",
                "title": "还没有按日期安排地点",
                "detail": "可以先选地点，再由你确认每一天的时间。",
                "action": "CHAT",
                "action_label": "开始选地点",
                "prompt": "先帮我探索这段旅行适合去的地点，给我一些候选并询问偏好；不要替我安排日期和时间。",
            })
            continue
        day, _, issue = finding.partition(" ")
        if len(day) != 10 or day[4] != "-" or day[7] != "-":
            day, issue = "", finding
        if issue.startswith(("尚未明确安排午餐", "尚未明确安排晚餐")):
            meal = "午餐" if "午餐" in issue else "晚餐"
            if day:
                missing_meals[meal].append(day)
        elif issue.startswith(("目前没有任何已安排地点", "尚未按日期安排")):
            items.append({
                "key": f"DAY:{day}",
                "title": f"{day} · 还没有安排",
                "detail": "可以从当地热门地点开始，再按你的偏好筛选。",
                "action": "CHAT",
                "action_label": "探索这一天",
                "prompt": f"帮我为{day}推荐几个合适的旅行地点；先给候选并询问我的偏好，不要替我安排时间。",
            })
        elif "有约" in issue and "小时空档" in issue:
            gap = issue.split("有约", 1)[0].strip()
            items.append({
                "key": f"GAP:{day}:{gap}",
                "title": f"{day} · {gap}有一段空档",
                "detail": "可以补充附近活动，也可以保留休息时间。",
                "action": "CHAT",
                "action_label": "处理空档",
                "prompt": f"我的行程在{day} {gap}有空档。请先判断附近是否适合增加活动，再询问我想补充地点还是保留休息，不要默认修改计划。",
            })
        elif "尚未添加住宿备选" in issue:
            items.append({
                "key": "LODGING",
                "title": "还没有住宿备选",
                "detail": "住宿搜索只会提供候选，不会代替预订。",
                "action": "CHAT",
                "action_label": "找住宿",
                "prompt": "帮我搜索这段旅行的住宿候选；如果缺少预算、区域或入住人数，请先问我。",
            })
        elif "没有出发地信息" in issue:
            items.append({
                "key": "ORIGIN",
                "title": "还没有填写出发地",
                "detail": "填写后才能估算到首个地点的交通时间。",
                "action": "EDIT_ORIGIN",
                "action_label": "填写出发地",
                "prompt": "",
            })
        elif "交通路线尚未核验" in issue:
            items.append({
                "key": "ROUTES",
                "title": "地点间路线尚未核验",
                "detail": "核验路线后，时间安排会更可靠。",
                "action": "CHAT",
                "action_label": "核验路线",
                "prompt": "请核验当前行程中已保存地点之间的路线；如果用户说明步行则查询步行，否则按明确要求处理；如果方式不支持或无法核验，请说明限制。",
            })
    meal_items = []
    for meal, days in missing_meals.items():
        if not days:
            continue
        dates = "、".join(days)
        meal_items.append({
            "key": f"MEAL:{meal}",
            "title": f"有 {len(days)} 天{meal}还没安排",
            "detail": f"日期：{dates}。可以先看餐饮候选，再由你选择地点和时间。",
            "action": "CHAT",
            "action_label": f"找{meal}",
            "prompt": f"请为{dates}推荐{meal}地点，只展示餐饮候选；我选择后再确认具体时间。",
        })
    draft = getattr(row, "draft", None)
    current_context = context or row.context or {}
    if isinstance(draft, dict) and draft.get("context_revision") == current_context.get("revision"):
        impacts = (draft.get("weather") or {}).get("plan_impacts", [])
        for impact in impacts if isinstance(impacts, list) else []:
            if not isinstance(impact, dict):
                continue
            day = impact.get("day")
            name = impact.get("name")
            candidate_id = impact.get("candidate_id")
            if not all(isinstance(value, str) and value for value in (day, name, candidate_id)):
                continue
            try:
                trip_day = date.fromisoformat(day)
            except ValueError:
                continue
            day_number = (trip_day - row.start_date).days + 1 if row.start_date else None
            items.append({
                "key": f"WEATHER:{day}:{candidate_id}",
                "title": f"{day} · {name} 可能受雨影响",
                "detail": (
                    str(impact.get("reason", "预报天气可能影响这个已安排的户外地点。"))
                    + " 地图只核验地点信息；是否为室内场所、当天是否开放仍需确认。"
                ),
                "action": "CHAT",
                "action_label": "找室内备选",
                "prompt": f"第{day_number or ''}天的{name}可能受雨影响，请搜索可作为室内备选的地点并说明地图能核验和不能核验的信息。先让我选择候选；我选定后再用路线数据生成替换时间草案，不要自动替换或保存正式行程。",
            })
    return [*meal_items, *items][:12]


def draft_meal_follow_up_options(
    suggestions: list[dict], candidates: list[dict],
    start_date: date | None, end_date: date | None,
) -> list[dict[str, str]]:
    """Find meal periods absent from a non-persistent schedule proposal.

    This only creates optional UI prompts. It never changes the draft or starts
    a provider call; the user explicitly chooses whether to search for food.
    """
    if not suggestions:
        return []
    by_id = {str(item.get("id")): item for item in candidates if item.get("id")}
    scheduled: dict[str, list[tuple[int, int, bool, str]]] = {}
    for suggestion in suggestions[:100]:
        candidate = by_id.get(str(suggestion.get("candidate_id")))
        day = suggestion.get("day")
        start = suggestion.get("start_time")
        if candidate is None or not isinstance(day, str) or not isinstance(start, str):
            continue
        try:
            hour, minute = map(int, start[:5].split(":"))
            begin = hour * 60 + minute
        except (TypeError, ValueError):
            continue
        duration = candidate.get("duration_min", 60)
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 24 * 60:
            duration = 60
        category = str(candidate.get("planning_category") or "").upper()
        scheduled.setdefault(day, []).append((
            begin, begin + duration, category == "FOOD", str(candidate.get("name") or "地点")[:80],
        ))

    if not scheduled:
        return []

    if start_date and end_date and end_date >= start_date:
        days = [
            date.fromordinal(start_date.toordinal() + offset).isoformat()
            for offset in range((end_date - start_date).days + 1)
        ][:31]
    else:
        days = sorted(scheduled)
    options: list[dict[str, str]] = []
    for meal, label, window_start, window_end in (
        ("LUNCH", "午餐", 11 * 60, 14 * 60),
        ("DINNER", "晚餐", 17 * 60, 20 * 60),
    ):
        missing_days = []
        for day in days:
            entries = scheduled.get(day, [])
            covered = any(
                is_food and window_start <= begin < window_end
                for begin, _end, is_food, _name in entries
            )
            if not covered:
                missing_days.append(day)
        if missing_days:
            dates = "、".join(missing_days[:7])
            options.append({
                "key": f"DRAFT_MEAL:{meal}",
                "title": f"草案里的{label}还没安排",
                "detail": f"{dates}。搜索不会写入正式行程；选点后需要重新检查草案。",
                "action_label": f"推荐{label}",
                "prompt": (
                    f"请为{dates}推荐{label}地点，只搜索餐饮候选，不写入正式行程，也不要自动选点或安排时间；"
                    "我选好后再确认具体日期和时间。"
                ),
            })
    gap_options = []
    for day, entries in sorted(scheduled.items()):
        ordered = sorted(entries)
        for previous, current in pairwise(ordered):
            gap = current[0] - previous[1]
            if gap < 180:
                continue
            previous_time = f"{previous[1] // 60:02d}:{previous[1] % 60:02d}"
            current_time = f"{current[0] // 60:02d}:{current[0] % 60:02d}"
            gap_options.append({
                "key": f"DRAFT_GAP:{day}:{previous[1]}:{current[0]}",
                "title": "草案里有一段较长空档",
                "detail": (
                    f"{day} {previous_time}–{current_time}，位于“{previous[3]}”和“{current[3]}”之间。"
                    "可以找目的地内的活动候选，也可以保留休息。"
                ),
                "action_label": "找地点候选",
                "prompt": (
                    f"我的待确认草案在{day} {previous_time}至{current_time}之间有空档，"
                    f"前后地点是“{previous[3]}”和“{current[3]}”。请为这段空档推荐地点候选，"
                    "先核对候选和路线依据，不要默认地点就在附近，不要修改当前草案或正式行程，也不要替我安排时间。"
                ),
            })
    return [*options, *gap_options[:2]][:4]
