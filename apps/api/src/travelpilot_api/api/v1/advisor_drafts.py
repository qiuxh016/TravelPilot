import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from itertools import pairwise
from math import ceil
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.verifiers import verify_distance_matrix
from travelpilot_core.engines.constraints.negotiation import (
    NegotiationActivity,
    negotiate_schedule,
)
from travelpilot_core.engines.optimizer.itinerary import Candidate, Visit
from travelpilot_core.ports.tools import ToolFailure
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
)
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_infra.providers.weather.open_meteo import WeatherProviderError

from travelpilot_api.advisor.context import (
    profile_memory_fingerprint,
    sync_personal_memory_snapshot,
)
from travelpilot_api.advisor.draft_service import (
    meal_schedule_advisories,
    overlaps_time_window,
    rest_check_item,
    validate_draft_selection,
)
from travelpilot_api.advisor.interpretation import (
    _active_personal_memories,
    _clock_minutes,
    _normalized_place_name,
)
from travelpilot_api.advisor.policies import (
    classify_draft_memory_conflicts,
    overridden_profile_memory_texts,
)
from travelpilot_api.advisor.route_evidence import reusable_route_evidence
from travelpilot_api.advisor.sessions import owned_session, read_session
from travelpilot_api.advisor.tools import (
    distance_tool_runtime,
    map_evidence_source,
)
from travelpilot_api.advisor.weather_advice import daily_weather_advice, planned_weather_impacts
from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider, get_weather_provider
from travelpilot_api.schemas.advisor import (
    AdvisorDraftCreate,
    AdvisorNegotiationRead,
    AdvisorNegotiationRequest,
    AdvisorSessionRead,
)

drafts_router = APIRouter(tags=["advisor"])
_DRAFT_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{8,80}")


def _draft_request_ref(payload: AdvisorDraftCreate) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

@drafts_router.post("/sessions/{session_id}/draft", response_model=AdvisorSessionRead)
async def create_draft(
    session_id: UUID,
    payload: AdvisorDraftCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    weather=Depends(get_weather_provider),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
):
    started_at = perf_counter()
    row = await owned_session(db, session_id, user.id, lock=True)
    context_snapshot = row.context if isinstance(row.context, dict) else {}
    current_context_revision = int(context_snapshot.get("revision", 0))
    current_draft = row.draft if isinstance(row.draft, dict) else None
    if payload.expected_draft_version is not None and (
        payload.expected_draft_version != row.draft_version
        or current_draft is None
        or current_draft.get("version") != row.draft_version
    ):
        raise HTTPException(409, detail="行程草案已被更新，请刷新当前安排后再试")
    if (
        payload.expected_context_revision is not None
        and payload.expected_context_revision != current_context_revision
    ):
        raise HTTPException(409, detail="旅行条件已被更新，请刷新当前安排后再试")
    key_ref = None
    request_ref = None
    if idempotency_key is not None:
        if not _DRAFT_IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise HTTPException(422, detail="Idempotency-Key 格式无效")
        key_ref = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_ref = _draft_request_ref(payload)
        prior_request = await db.scalar(select(AdvisorRunEventModel).where(
            AdvisorRunEventModel.owner_id == user.id,
            AdvisorRunEventModel.session_id == row.id,
            AdvisorRunEventModel.phase == "planning",
            AdvisorRunEventModel.operation == "create_draft_idempotency",
            AdvisorRunEventModel.metadata_json["key_ref"].as_string() == key_ref,
        ).order_by(AdvisorRunEventModel.created_at.desc()).limit(1))
        if prior_request is not None:
            metadata = prior_request.metadata_json if isinstance(prior_request.metadata_json, dict) else {}
            if metadata.get("request_ref") != request_ref:
                raise HTTPException(409, detail="Idempotency-Key 已用于不同的草案请求")
            prior_version = metadata.get("draft_version")
            if (
                isinstance(prior_version, bool) or not isinstance(prior_version, int)
                or prior_version != row.draft_version
                or not isinstance(row.draft, dict)
                or row.draft.get("version") != prior_version
            ):
                raise HTTPException(409, detail="该草案请求已完成，但当前草案版本已更新；请发起新的草案请求")
            return await read_session(db, row)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能重新生成草案")
    if not row.start_date or not row.end_date:
        raise HTTPException(400, detail="请先设置旅行开始和结束日期")
    if not row.destination:
        raise HTTPException(400, detail="请先设置旅行目的地")
    profile_memories = await _active_personal_memories(db, user.id)
    current_context = dict(row.context or {})
    current_context, memories_changed = sync_personal_memory_snapshot(
        current_context, profile_memories,
    )
    if memories_changed:
        row.context = current_context
    planning_inputs = (row.context or {}).get("planning_inputs", {})
    explicit_limits = [{"constraint_type": "HARD_LIMIT", "text": text}
                       for text in planning_inputs.get("hard_constraints", [])]
    planning_memories = [*profile_memories, *explicit_limits]
    selected, schedule_by_id = validate_draft_selection(
        row.candidates, payload.candidate_ids, payload.schedules, row.start_date, row.end_date,
    )
    food_candidate_ids = {
        str(item["id"]) for item in selected if item.get("planning_category") == "FOOD"
    }
    planning_category_by_candidate = {
        str(item["id"]): item.get("planning_category", "ATTRACTION") for item in selected
    }
    category_text_by_candidate = {
        str(item["id"]): f"{item.get('name', '')} {item.get('category', '')}".casefold()
        for item in selected
    }
    profile_conflicts, trip_constraint_conflicts = classify_draft_memory_conflicts(
        selected, profile_memories, explicit_limits,
    )
    if trip_constraint_conflicts:
        raise HTTPException(409, detail="行程硬性限制冲突：" + "；".join(trip_constraint_conflicts))
    if profile_conflicts and not payload.allow_profile_memory_conflicts:
        raise HTTPException(
            409,
            detail="PROFILE_MEMORY_CONFLICT：这些地点与你确认的长期偏好记忆冲突："
            + "；".join(profile_conflicts)
            + "。如果这是你本次明确想去的地点，可仅对本次草案确认继续；不会修改或删除长期记忆。",
        )
    overridden_memory_texts = overridden_profile_memory_texts(
        selected, profile_memories,
        explicitly_overridden=payload.allow_profile_memory_conflicts,
    )
    planner_candidates = [Candidate(
        poi_id=UUID(item["id"]), name=item["name"], latitude=float(item["latitude"]),
        longitude=float(item["longitude"]), duration_min=max(15, int(item["duration_min"])),
        priority=3, interest_score=float(item.get("confidence", 0.5)),
        opening_hours=item.get("opening_hours"), note=item.get("suggestion"),
    ) for item in selected if item.get("planning_category") != "LODGING"]
    matrix = None
    start_travel_min = None
    origin_leg = {"status": "NOT_PROVIDED", "from": None, "duration_min": None,
                  "source": None, "retrieved_at": None}
    transport_provenance = {
        "status": "NOT_REQUIRED", "source": None, "retrieved_at": None,
    }
    distance_runtime = None
    origin_query = str(planning_inputs.get("origin", "")).strip()
    route_cache = (row.context or {}).get("agent_route_evidence")
    if not origin_query and isinstance(route_cache, dict):
        selected_cache_ids = [str(candidate.poi_id) for candidate in planner_candidates]
        reusable_cache = reusable_route_evidence(
            route_cache,
            selected_cache_ids,
            int((row.context or {}).get("revision", 0)),
            expected_source=map_evidence_source(maps),
        )
        if reusable_cache is not None:
            matrix = reusable_cache["durations_min"]
            transport_provenance = {
                "status": reusable_cache["status"],
                "source": reusable_cache["source"],
                "retrieved_at": reusable_cache["retrieved_at"],
            }
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
            distance_runtime = distance_tool_runtime(maps)
            result = await distance_runtime.call("get_distance_matrix", {
                "points": [origin_point, *((c.latitude, c.longitude) for c in planner_candidates)],
            })
            full_matrix = result["durations_min"]
            if not verify_distance_matrix({"durations_min": full_matrix}, len(planner_candidates) + 1):
                raise ToolFailure("MATRIX_VERIFICATION_FAILED")
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
            distance_runtime = distance_tool_runtime(maps)
            result = await distance_runtime.call("get_distance_matrix", {
                "points": [(c.latitude, c.longitude) for c in planner_candidates],
            })
            if not verify_distance_matrix(result, len(planner_candidates)):
                raise ToolFailure("MATRIX_VERIFICATION_FAILED")
            matrix = result["durations_min"]
            transport_provenance = {key: result[key] for key in ("status", "source", "retrieved_at")}
        except ToolFailure as exc:
            transport_provenance = {
                "status": "UNAVAILABLE", "source": None, "retrieved_at": None,
            }
            warnings_for_transport = (
                "地图交通查询超时；草案使用本地距离估算，确认前请检查路线和时间留白。"
                if exc.code == "TOOL_TIMEOUT"
                else "地图交通服务暂不可用；草案使用本地距离估算，确认前请检查路线和时间留白。"
            )
        except MapsProviderError:
            transport_provenance = {
                "status": "UNAVAILABLE", "source": None, "retrieved_at": None,
            }
            warnings_for_transport = "地图交通服务暂不可用；草案使用本地距离估算，确认前请检查路线和时间留白。"
        else:
            warnings_for_transport = None
    else:
        warnings_for_transport = None
    max_per_day = {1: 3, 2: 4, 3: 5, 4: 6, 5: 6}[row.pace_level]
    warnings = []
    warnings.extend(meal_schedule_advisories(selected, schedule_by_id))
    if profile_conflicts and payload.allow_profile_memory_conflicts:
        warnings.append(
            "本次草案按你的明确确认临时覆盖了长期偏好记忆；该记忆未被修改，后续旅行仍会继续参考。"
        )
    if warnings_for_transport:
        warnings.append(warnings_for_transport)
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
    attention_items = []
    transitions = []
    for day, day_visits in daily_counts.items():
        attraction_count = sum(
            planning_category_by_candidate.get(str(visit.candidate.poi_id)) == "ATTRACTION"
            for visit in day_visits
        )
        if attraction_count > max_per_day:
            warnings.append(f"阻断：{day} 有 {attraction_count} 个游览地点，超过当前节奏建议上限 {max_per_day} 个")
        for previous, current in pairwise(day_visits):
            previous_index = next(i for i, candidate in enumerate(planner_candidates) if candidate.poi_id == previous.candidate.poi_id)
            current_index = next(i for i, candidate in enumerate(planner_candidates) if candidate.poi_id == current.candidate.poi_id)
            travel_min = (matrix[previous_index][current_index] if matrix is not None else
                          max(5, round((((previous.candidate.latitude-current.candidate.latitude)**2 + ((previous.candidate.longitude-current.candidate.longitude)*.86)**2)**.5) * 111 * 2.5)))
            transitions.append({
                "day": day.isoformat(),
                "from_candidate_id": str(previous.candidate.poi_id),
                "from_name": previous.candidate.name,
                "to_candidate_id": str(current.candidate.poi_id),
                "to_name": current.candidate.name,
                "duration_min": travel_min,
                "status": transport_provenance["status"]
                if transport_provenance["status"] in ("VERIFIED", "SIMULATED")
                else "ESTIMATED",
                "source": transport_provenance["source"],
            })
            if _clock_minutes(previous.end_time) + travel_min + 15 > _clock_minutes(current.start_time):
                warnings.append(f"阻断：{previous.candidate.name} 到 {current.candidate.name} 的预计游览、交通和 15 分钟换乘缓冲冲突（交通约 {travel_min} 分钟）")
            elif travel_min >= 90:
                warnings.append(f"{previous.candidate.name} 到 {current.candidate.name} 距离较远，地图估算交通约 {travel_min} 分钟，请确认路线是否合适")
        for previous, current in pairwise(day_visits):
            gap = _clock_minutes(current.start_time) - _clock_minutes(previous.end_time)
            if gap >= 180:
                warnings.append(f"{day} {previous.end_time.strftime('%H:%M')}–{current.start_time.strftime('%H:%M')} 有约 {gap // 60} 小时空档，可安排休息或补充地点")
                attention_items.append({
                    "kind": "OPEN_GAP", "day": day.isoformat(),
                    "from": previous.end_time.strftime("%H:%M"),
                    "to": current.start_time.strftime("%H:%M"),
                    "hours": gap // 60,
                })
        meal_terms = ("餐饮", "餐厅", "餐馆", "美食", "小吃", "火锅", "饭店", "快餐", "restaurant", "food")
        lunch_covered = any(overlaps_time_window(
                                _clock_minutes(visit.start_time), _clock_minutes(visit.end_time),
                                11 * 60, 14 * 60,
                            )
                            and (str(visit.candidate.poi_id) in food_candidate_ids or any(
                                term in category_text_by_candidate.get(str(visit.candidate.poi_id), "")
                                for term in meal_terms
                            ))
                            for visit in day_visits)
        dinner_covered = any(overlaps_time_window(
                                 _clock_minutes(visit.start_time), _clock_minutes(visit.end_time),
                                 17 * 60, 20 * 60,
                             )
                             and (str(visit.candidate.poi_id) in food_candidate_ids or any(
                                 term in category_text_by_candidate.get(str(visit.candidate.poi_id), "")
                                 for term in meal_terms
                             ))
                             for visit in day_visits)
        if not lunch_covered:
            warnings.append(f"{day} 尚未明确安排午餐")
            attention_items.append({"kind": "MEAL_MISSING", "day": day.isoformat(), "meal_type": "LUNCH"})
        if not dinner_covered:
            warnings.append(f"{day} 尚未明确安排晚餐")
            attention_items.append({"kind": "MEAL_MISSING", "day": day.isoformat(), "meal_type": "DINNER"})
        rest_item = rest_check_item(day, [
            (visit.start_time, visit.end_time) for visit in day_visits
        ])
        if rest_item:
            warnings.append(
                f"{day} 的安排跨度较长，地点间未检测到 45 分钟以上的空档；请确认是否需要预留休息时间。"
            )
            attention_items.append(rest_item)
    scheduled_days = set(daily_counts)
    selected_lodging = [item for item in selected if item.get("planning_category") == "LODGING"]
    if row.start_date < row.end_date and not selected_lodging:
        attention_items.append({"kind": "LODGING_MISSING", "day": row.start_date.isoformat()})
        warnings.append("多日旅行尚未添加住宿地点；如果你已有安排或不需要住宿，可以忽略此项。")
    if visits and not str(planning_inputs.get("origin", "")).strip():
        attention_items.append({"kind": "ORIGIN_MISSING", "day": row.start_date.isoformat()})
        warnings.append("尚未设置出发地，首段出发交通时间未纳入排程。")
    if len(planner_candidates) > 1 and transport_provenance["status"] == "UNAVAILABLE":
        attention_items.append({"kind": "TRANSPORT_UNVERIFIED", "day": row.start_date.isoformat()})
    for offset in range((row.end_date - row.start_date).days + 1):
        trip_day = date.fromordinal(row.start_date.toordinal() + offset)
        if trip_day not in scheduled_days:
            warnings.append(f"{trip_day} 尚无已选安排，整天空档由你决定如何使用")
            attention_items.append({"kind": "EMPTY_DAY", "day": trip_day.isoformat()})
            attention_items.extend([
                {"kind": "MEAL_MISSING", "day": trip_day.isoformat(), "meal_type": "LUNCH"},
                {"kind": "MEAL_MISSING", "day": trip_day.isoformat(), "meal_type": "DINNER"},
            ])
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
        if memory in profile_memories and str(memory.get("text", "")) in overridden_memory_texts:
            continue
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
            "days": [
                {**day, "advice": daily_weather_advice(day)}
                for day in forecast.get("days", [])
            ],
        }
    except WeatherProviderError:
        weather_summary = {
            "status": "UNAVAILABLE", "city": row.destination, "timezone": None,
            "source": "Open-Meteo", "attribution": "Weather data by Open-Meteo (CC BY 4.0)",
            "fetched_at": datetime.now(UTC).isoformat(), "message": "天气服务暂时不可用。",
            "days": [],
        }
        warnings.append("天气服务暂时不可用；草案未使用天气信息。")
    weather_summary["plan_impacts"] = planned_weather_impacts(
        selected, schedule_by_id, weather_summary.get("days", []),
    )
    for impact in weather_summary["plan_impacts"]:
        warnings.append(
            f"天气提醒：{impact['day']} 的 {impact['name']} 可能受降水影响；"
            "可查看室内备选，行程不会自动修改。"
        )
    context = dict(row.context or {})
    selected_ids = [item["id"] for item in selected]
    if set(context.get("selected_candidate_ids", [])) != set(selected_ids):
        context["selected_candidate_ids"] = selected_ids
        context["revision"] = int(context.get("revision", 0)) + 1
    if matrix is not None and transport_provenance["status"] in {"VERIFIED", "SIMULATED"}:
        context["agent_route_evidence"] = {
            "candidate_ids": [str(candidate.poi_id) for candidate in planner_candidates],
            "context_revision": int(context.get("revision", 0)),
            "durations_min": matrix,
            "status": transport_provenance["status"],
            "source": transport_provenance["source"],
            "retrieved_at": transport_provenance["retrieved_at"],
            "mode": "DRIVING",
        }
    row.context = context
    selected_by_id = {item["id"]: item for item in selected}
    after_stops = [{
        "candidate_id": str(visit.candidate.poi_id),
        "poi_id": selected_by_id[str(visit.candidate.poi_id)].get("existing_poi_id"),
        "status": selected_by_id[str(visit.candidate.poi_id)].get("plan_status", "PLANNED"),
        "name": visit.candidate.name, "day": visit.day.isoformat(),
        "start_time": visit.start_time.isoformat(), "end_time": visit.end_time.isoformat(),
    } for visit in visits]
    before_stops = (context.get("current_plan") or {}).get("stops", [])
    before_by_key = {
        item["poi_id"]: item for item in before_stops
        if item.get("planning_category") != "LODGING"
    }
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
        "profile_memory_fingerprint": profile_memory_fingerprint(profile_memories),
        "transport": transport_provenance,
        "transitions": transitions,
        "origin_leg": origin_leg,
        "warnings": warnings,
        "attention_items": attention_items,
        "lodging": [{
            "candidate_id": str(item["id"]), "name": item["name"],
            "address": item.get("address"), "provider": item.get("provider"),
            "verification_status": item.get("verification_status"),
        } for item in selected_lodging],
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
            "status": selected_by_id[str(visit.candidate.poi_id)].get("plan_status", "PLANNED"),
            "day": visit.day.isoformat(), "start_time": visit.start_time.isoformat(),
            "end_time": visit.end_time.isoformat(), "latitude": visit.candidate.latitude,
            "longitude": visit.candidate.longitude, "category": selected_by_id[str(visit.candidate.poi_id)]["category"],
            "planning_category": selected_by_id[str(visit.candidate.poi_id)].get("planning_category", "ATTRACTION"),
            "meal_type": selected_by_id[str(visit.candidate.poi_id)].get("meal_type"),
            "address": selected_by_id[str(visit.candidate.poi_id)]["address"],
            "note": (
                f"用餐安排：{ {'BREAKFAST': '早餐', 'LUNCH': '午餐', 'DINNER': '晚餐', 'SNACK': '小吃/加餐'}.get(selected_by_id[str(visit.candidate.poi_id)].get('meal_type'), '用餐') }"
                if selected_by_id[str(visit.candidate.poi_id)].get("planning_category") == "FOOD"
                else "住宿安排"
                if selected_by_id[str(visit.candidate.poi_id)].get("planning_category") == "LODGING"
                else visit.candidate.note
            ),
        } for visit in visits],
    }
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant",
                               content=(
                                   f"已生成第 {row.draft_version} 版待确认草案，安排 {len(visits)} 个地点。确认前不会写入旅行数据。"
                                   + (" 我还发现部分日期没有午餐、晚餐或存在较长空档。你可以让我推荐地点、导入攻略，或稍后再定。" if attention_items else "")
                               ),
                               payload={
                                   "draft_version": row.draft_version, "warnings": warnings,
                                   "attention_items": attention_items,
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
    if key_ref is not None and request_ref is not None:
        db.add(AdvisorRunEventModel(
            owner_id=user.id,
            session_id=row.id,
            phase="planning",
            operation="create_draft_idempotency",
            status="SUCCEEDED",
            metadata_json={
                "key_ref": key_ref,
                "request_ref": request_ref,
                "draft_version": row.draft_version,
            },
        ))
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await read_session(db, row)

def _opening_minute(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


@drafts_router.post(
    "/sessions/{session_id}/negotiate",
    response_model=AdvisorNegotiationRead,
)
async def negotiate_draft(
    session_id: UUID,
    payload: AdvisorNegotiationRequest,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await owned_session(db, session_id, user.id, lock=True)
    draft = row.draft if isinstance(row.draft, dict) else None
    if row.status != "ACTIVE" or draft is None:
        raise HTTPException(409, detail="请先生成仍有效的行程安排")
    if row.start_date is None or row.end_date is None:
        raise HTTPException(409, detail="旅行日期尚未设置，无法检查安排")
    context = row.context if isinstance(row.context, dict) else {}
    revision = int(context.get("revision", 0))
    if (
        payload.draft_version != row.draft_version
        or draft.get("version") != row.draft_version
        or draft.get("context_revision") != revision
    ):
        raise HTTPException(409, detail="行程或旅行条件已更新，请先刷新当前安排")

    stops = draft.get("stops")
    if not isinstance(stops, list) or not stops:
        raise HTTPException(409, detail="当前没有可检查的时间安排")
    stop_ids = [str(stop.get("candidate_id", "")) for stop in stops if isinstance(stop, dict)]
    if len(stop_ids) != len(stops) or len(set(stop_ids)) != len(stop_ids):
        raise HTTPException(409, detail="当前安排数据不完整，无法安全试算")
    candidates = {
        str(candidate.get("id")): candidate
        for candidate in (row.candidates or [])
        if isinstance(candidate, dict)
    }
    activities: list[NegotiationActivity] = []
    unverified_hours = 0
    for stop in stops:
        candidate_id = str(stop["candidate_id"])
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise HTTPException(409, detail="当前安排缺少地点信息，请刷新后重试")
        try:
            day = date.fromisoformat(str(stop["day"]))
            start_time = time.fromisoformat(str(stop["start_time"]))
            end_time = time.fromisoformat(str(stop["end_time"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, detail="当前安排的日期或时间无效，无法安全试算") from exc
        start_minute = start_time.hour * 60 + start_time.minute
        end_minute = end_time.hour * 60 + end_time.minute
        if end_minute <= start_minute:
            raise HTTPException(409, detail="当前安排的结束时间无效，无法安全试算")
        opening_hours = candidate.get("opening_hours")
        opening_hours = opening_hours if isinstance(opening_hours, dict) else {}
        opens_at = _opening_minute(opening_hours.get("open"))
        closes_at = _opening_minute(opening_hours.get("close"))
        if opens_at is None or closes_at is None:
            unverified_hours += 1
        meal_type = str(stop.get("meal_type") or candidate.get("meal_type") or "").upper()
        category = (
            "LUNCH"
            if meal_type == "LUNCH"
            else "DINNER"
            if meal_type == "DINNER"
            else "OTHER"
            if stop.get("planning_category") == "FOOD"
            else "ATTRACTION"
        )
        fixed_schedule = candidate.get("fixed_schedule")
        activities.append(
            NegotiationActivity(
                id=candidate_id,
                name=str(stop.get("name") or candidate.get("name") or "地点"),
                day=day,
                start_minute=start_minute,
                duration_minute=end_minute - start_minute,
                priority=int(candidate.get("priority", 3))
                if str(candidate.get("priority", 3)).isdigit()
                else 3,
                optional=candidate.get("optional") is True,
                required=candidate.get("required") is True,
                fixed=isinstance(fixed_schedule, dict) and bool(fixed_schedule),
                category=category,
                opens_at=opens_at,
                closes_at=closes_at,
            )
        )

    travel_minutes: dict[tuple[str, str], int] = {}
    if len(stop_ids) > 1:
        cache = context.get("agent_route_evidence")
        evidence = reusable_route_evidence(cache, stop_ids, revision)
        if evidence is None:
            raise HTTPException(
                409,
                detail="路线证据缺失或已过期；请先重新校验路线，再生成冲突方案",
            )
        matrix = evidence["durations_min"]
        travel_minutes = {
            (stop_ids[i], stop_ids[j]): ceil(float(matrix[i][j]))
            for i in range(len(stop_ids))
            for j in range(len(stop_ids))
            if i != j
        }
    result = negotiate_schedule(
        activities,
        start_date=row.start_date,
        end_date=row.end_date,
        travel_minutes=travel_minutes,
    )
    return AdvisorNegotiationRead(
        draft_version=row.draft_version,
        status=(
            "NO_CONFLICT"
            if result.feasible and not result.conflicts
            else "OPTIONS"
            if result.alternatives
            else "NO_FEASIBLE_OPTION"
        ),
        conflicts=[conflict.message for conflict in result.conflicts],
        notes=(
            [f"{unverified_hours} 个地点缺少可核验的营业时间，本次未将其作为已检查条件。"]
            if unverified_hours
            else []
        ),
        alternatives=[
            {
                "id": alternative.id,
                "title": alternative.title,
                "summary": alternative.summary,
                "change_cost": alternative.change_cost,
                "changes": [
                    {
                        "candidate_id": str(change["activity_id"]),
                        "operation": str(change["operation"]),
                        "day": change.get("after_day"),
                        "start_time": change.get("after_start"),
                    }
                    for change in alternative.changes
                ],
            }
            for alternative in result.alternatives
        ],
    )
