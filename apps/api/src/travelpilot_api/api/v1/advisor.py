import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.agents.extraction import extract_places
from travelpilot_core.engines.optimizer.itinerary import Candidate, build_itinerary
from travelpilot_infra.db.models.advisor import AdvisorMessageModel, AdvisorSessionModel
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import MemoryModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.schemas.advisor import (
    AdvisorConfirm,
    AdvisorConfirmRead,
    AdvisorDraftCreate,
    AdvisorMessageCreate,
    AdvisorSessionCreate,
    AdvisorSessionRead,
)
from travelpilot_api.settings import settings

router = APIRouter(prefix="/advisor", tags=["advisor"])


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


def _apply_existing_adjustments(row: AdvisorSessionModel, content: str) -> tuple[dict, list[str]]:
    context = dict(row.context or {})
    selected = set(context.get("selected_candidate_ids", []))
    original_selected = set(selected)
    messages: list[str] = []
    negative_terms = ("不要", "不去", "不想去", "移除", "删掉", "删除", "去掉", "取消", "排除")
    positive_terms = ("保留", "还要去", "仍想去", "继续去", "安排", "加入", "想去", "要去")
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
        negative_found = any(re.search(term, nearby) for term in negative_terms)
        intents = [
            (abs((match.start() + len(term)) - center), "positive")
            for term in positive_terms
            for match in re.finditer(term, nearby)
        ]
        intent = "negative" if negative_found else min(intents, default=(0, "none"), key=lambda item: item[0])[1]
        if intent == "negative":
            if candidate["id"] in selected:
                selected.remove(candidate["id"])
                messages.append(f"已从草案移除{name}")
        elif intent == "positive" and candidate["id"] not in selected:
            selected.add(candidate["id"])
            messages.append(f"已将{name}加入草案候选")

    lower_pace = ("轻松一点", "轻松一些", "慢一点", "慢一些", "松一点", "多休息", "多留休息", "休息时间", "少安排", "别太赶", "不要太赶", "减少安排")
    higher_pace = ("紧凑一点", "快一点", "多安排", "安排紧凑", "节奏快")
    if any(term in content for term in lower_pace) and row.pace_level > 1:
        row.pace_level -= 1
        messages.append(f"已将节奏调整为 {row.pace_level}/5")
    elif any(term in content for term in higher_pace) and row.pace_level < 5:
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
        }
    welcome = (
        "已载入你自己的旅行上下文。告诉我想保留、增加或调整的地点；我会先核对地点，再生成待确认草案。"
        if trip else
        "会话已创建。告诉我想去的具体地点或偏好；我会用地图服务核验地点。日期、预算和节奏会作为排程约束。"
    )
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant", content=welcome))
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


@router.post("/sessions/{session_id}/messages", response_model=AdvisorSessionRead)
async def send_message(
    session_id: UUID,
    payload: AdvisorMessageCreate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
):
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    content = payload.content.strip()
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="user", content=content))
    if content and ("偏好" in content or "喜欢" in content or "不要" in content or "希望" in content):
        row.preference_text = (row.preference_text + "\n" + content).strip()[-4000:]
    try:
        context, adjustments = _apply_existing_adjustments(row, content) if row.trip_id else (dict(row.context or {}), [])
        extraction = await extract_places(
            content, api_key=settings.llm_api_key, model=settings.llm_model, base_url=settings.llm_base_url,
        )
        found = []
        searched_mentions = 0
        for mention in extraction.places:
            if row.trip_id and any(candidate["name"] == mention.name for candidate in row.candidates):
                continue
            searched_mentions += 1
            for place in (await maps.search_place(mention.name, row.destination or None))[:5]:
                candidate = {
                    "id": str(uuid4()), "provider": place.provider, "provider_id": place.provider_id,
                    "name": place.name, "address": place.address, "category": place.category,
                    "latitude": place.latitude, "longitude": place.longitude,
                    "duration_min": place.suggested_duration_min, "opening_hours": place.opening_hours,
                    "confidence": mention.confidence, "suggestion": mention.suggestion,
                }
                if not any(c["provider"] == candidate["provider"] and c["provider_id"] == candidate["provider_id"] for c in row.candidates):
                    row.candidates = [*row.candidates, candidate]
                    found.append(candidate)
                    if row.trip_id:
                        context["selected_candidate_ids"] = sorted({
                            *context.get("selected_candidate_ids", []), candidate["id"],
                        })
                        context["revision"] = int(context.get("revision", 0)) + 1
        if found:
            names = "、".join(c["name"] for c in found[:8])
            reply = f"地图服务核对到这些候选：{names}。请在候选清单中选择想去的地点，再生成行程草案。"
        elif searched_mentions:
            reply = "提取到了地点名称，但地图服务没有匹配结果。请补充城市或换一种地点写法。"
        elif adjustments:
            reply = "已记录你对当前计划的调整。确认变更前会生成新的草案版本，不会修改原计划。"
        else:
            model_note = "已使用配置的模型抽取地点名称" if settings.llm_api_key and settings.llm_model else "当前未配置模型，使用本地规则识别明确提到的地点"
            reply = f"{model_note}。请告诉我具体地点名称；偏好会保存在会话中，但地点需经地图核验后才能进入草案。"
        if adjustments:
            reply += " " + "；".join(adjustments) + "。"
        if row.trip_id and row.context and row.context.get("confirmed_memories") and not adjustments:
            reply += f" 会话已载入 {len(row.context['confirmed_memories'])} 条已确认旅行记忆供你查看。"
    except MapsProviderError as exc:
        await db.rollback()
        raise HTTPException(503, detail="地图服务暂时不可用，请稍后重试") from exc
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(502, detail="地点信息解析暂时失败，请重试或直接写出地点名称") from exc
    db.add(AdvisorMessageModel(session_id=row.id, owner_id=user.id, role="assistant", content=reply,
                               payload={"candidate_ids": [c["id"] for c in found]}))
    if row.trip_id:
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
):
    row = await _owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能重新生成草案")
    if not row.start_date or not row.end_date:
        raise HTTPException(400, detail="请先设置旅行开始和结束日期")
    by_id = {candidate["id"]: candidate for candidate in row.candidates}
    selected = [by_id.get(str(candidate_id)) for candidate_id in payload.candidate_ids]
    if any(candidate is None for candidate in selected) or len(set(map(str, payload.candidate_ids))) != len(payload.candidate_ids):
        raise HTTPException(400, detail="候选地点无效或重复")
    planner_candidates = [Candidate(
        poi_id=UUID(item["id"]), name=item["name"], latitude=float(item["latitude"]),
        longitude=float(item["longitude"]), duration_min=max(15, int(item["duration_min"])),
        priority=3, interest_score=float(item.get("confidence", 0.5)),
        opening_hours=item.get("opening_hours"), note=item.get("suggestion"),
    ) for item in selected]
    matrix = None
    if len(planner_candidates) > 1:
        try:
            matrix = await maps.get_distance_matrix([(c.latitude, c.longitude) for c in planner_candidates])
        except MapsProviderError as exc:
            raise HTTPException(503, detail="交通时间服务暂时不可用") from exc
    max_per_day = {1: 3, 2: 4, 3: 5, 4: 6, 5: 6}[row.pace_level]
    visits, warnings = build_itinerary(row.start_date, row.end_date, planner_candidates, matrix,
                                       buffer_min=90 if row.pace_level <= 2 else 60,
                                       max_pois_per_day=max_per_day)
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
        "warnings": warnings,
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
                               payload={"draft_version": row.draft_version, "warnings": warnings}))
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
    else:
        if not row.start_date or not row.end_date:
            raise HTTPException(400, detail="行程日期缺失")
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
    row.status = "CONFIRMED"
    row.confirmed_trip_id = trip.id
    row.confirmed_plan_id = plan.id
    row.updated_at = datetime.now(UTC)
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
