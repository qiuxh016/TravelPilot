import re
from datetime import UTC, date, datetime
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import (
    MemoryModel,
)
from travelpilot_infra.db.session import get_session

from travelpilot_api.advisor.checkpoints import checkpoint_writer
from travelpilot_api.advisor.context import (
    planning_state,
    refresh_trip_snapshot,
    sync_personal_memory_snapshot,
)
from travelpilot_api.advisor.destination_discovery import run_destination_discovery
from travelpilot_api.advisor.destination_service import (
    destination_evidence_callbacks,
    execute_destination_stage,
)
from travelpilot_api.advisor.interpretation import _candidate_planning_category
from travelpilot_api.advisor.orchestration_state import readonly_tool_decision_callbacks
from travelpilot_api.advisor.session_budget import (
    record_session_usage,
    remaining_session_output_tokens,
)
from travelpilot_api.advisor.sessions import owned_session, read_session
from travelpilot_api.advisor.state import (
    pending_agent_question,
    pending_agent_question_from_context,
)
from travelpilot_api.advisor.task_state import AdvisorTaskSnapshot
from travelpilot_api.advisor.tools import rank_map_places
from travelpilot_api.api.v1.advisor_confirm import confirm_router
from travelpilot_api.api.v1.advisor_drafts import drafts_router
from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.rate_limit import enforce_identity_rate_limit, get_rate_limit_redis
from travelpilot_api.schemas.advisor import (
    AdvisorChecklistDecision,
    AdvisorDestinationDiscoveryRequest,
    AdvisorRuntimeStatus,
    AdvisorSelectionUpdate,
    AdvisorSessionContextUpdate,
    AdvisorSessionCreate,
    AdvisorSessionRead,
)
from travelpilot_api.settings import settings

router = APIRouter(prefix="/advisor", tags=["advisor"])
router.include_router(drafts_router)
router.include_router(confirm_router)


@router.patch("/sessions/{session_id}/selection", response_model=AdvisorSessionRead)
async def update_session_selection(
    session_id: UUID,
    payload: AdvisorSelectionUpdate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    """Persist the live candidate selection and partial time inputs."""
    row = await owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")

    candidates = {
        str(candidate.get("id")): candidate
        for candidate in (row.candidates or [])
        if isinstance(candidate, dict) and candidate.get("id")
    }
    selected_ids = [str(candidate_id) for candidate_id in payload.selected_candidate_ids]
    if not set(selected_ids).issubset(candidates):
        raise HTTPException(422, detail="选择中包含不属于当前对话的地点")

    schedule_snapshot: dict[str, dict[str, str]] = {}
    for item in payload.schedules:
        candidate_id = str(item.candidate_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise HTTPException(422, detail="时间安排中包含不属于当前对话的地点")
        if candidate.get("planning_category") == "LODGING":
            raise HTTPException(422, detail="住宿候选不需要安排每日游览时间")
        if item.day and row.start_date and row.end_date and not row.start_date <= item.day <= row.end_date:
            raise HTTPException(422, detail="地点日期必须在旅行日期范围内")
        schedule_snapshot[candidate_id] = {
            **({"day": item.day.isoformat()} if item.day else {}),
            **({"start_time": item.start_time.strftime("%H:%M")} if item.start_time else {}),
        }

    context = dict(row.context or {})
    previous_ids = list(dict.fromkeys(str(value) for value in context.get("selected_candidate_ids", [])))
    previous_schedules = context.get("candidate_schedules", {})
    if not isinstance(previous_schedules, dict):
        previous_schedules = {}
    normalized_schedules = {
        str(candidate_id): value for candidate_id, value in previous_schedules.items()
        if str(candidate_id) in selected_ids and str(candidate_id) in schedule_snapshot
    }
    normalized_schedules.update(schedule_snapshot)
    if previous_ids != selected_ids or previous_schedules != normalized_schedules:
        context["selected_candidate_ids"] = selected_ids
        context["candidate_schedules"] = normalized_schedules
        context["revision"] = int(context.get("revision", 0)) + 1
        row.context = context
        row.updated_at = datetime.now(UTC)
        db.add(AdvisorRunEventModel(
            owner_id=user.id,
            session_id=row.id,
            phase="user",
            operation="update_selection",
            status="SUCCEEDED",
            metadata_json={
                "selected_count": len(selected_ids),
                "scheduled_count": len(normalized_schedules),
            },
        ))
        await db.commit()
        await db.refresh(row)
    return await read_session(db, row)


@router.get("/status", response_model=AdvisorRuntimeStatus)
async def advisor_runtime_status(_user: UserModel = Depends(get_current_user)):
    configured = bool(settings.llm_api_key.strip() and settings.llm_model.strip())
    return AdvisorRuntimeStatus(
        llm_status="CONFIGURED" if configured else "NOT_CONFIGURED",
        model_name=settings.llm_model.strip() if configured else None,
        output_token_limit=settings.llm_max_tokens_per_turn,
        session_output_token_limit=settings.llm_max_output_tokens_per_session,
    )


@router.patch("/sessions/{session_id}/checklist", response_model=AdvisorSessionRead)
async def update_checklist_decision(
    session_id: UUID,
    payload: AdvisorChecklistDecision,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await owned_session(db, session_id, user.id, lock=True)
    if not row.draft or row.draft_version != payload.draft_version:
        raise HTTPException(status_code=409, detail="草案已更新，请刷新后再处理这项检查。")
    context = dict(row.context or {})
    decisions = dict(context.get("checklist_decisions", {}))
    if payload.decision == "RESET":
        decisions.pop(payload.item_key, None)
    else:
        decisions[payload.item_key] = {
            "decision": payload.decision,
            "draft_version": payload.draft_version,
        }
    context["checklist_decisions"] = decisions
    row.context = context
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await read_session(db, row)


from travelpilot_api.advisor.interpretation import (
    _DESTINATION_CITIES,
    _active_personal_memories,
    _destination_queries,
)


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
    memory_context, _ = sync_personal_memory_snapshot({"revision": 0}, personal_memories)
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
        **memory_context,
        "selected_candidate_ids": [],
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
        plan_node_by_poi = {poi.id: node for node, poi in plan_rows}
        candidate_by_poi: dict[UUID, dict] = {}
        row.candidates = []
        for item, poi in existing_places:
            planned_node = plan_node_by_poi.get(poi.id)
            meal_type = next((kind for kind, label in (("BREAKFAST", "早餐"), ("LUNCH", "午餐"), ("DINNER", "晚餐"), ("SNACK", "小吃/加餐")) if planned_node and planned_node.note and label in planned_node.note), None)
            candidate = {
                "id": str(uuid4()), "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
                "existing_poi_id": str(poi.id), "in_current_plan": poi.id in plan_poi_ids,
                "name": poi.name, "address": poi.address, "category": poi.category,
                "planning_category": "FOOD" if meal_type else _candidate_planning_category(poi.category, poi.name),
                "meal_type": meal_type,
                "latitude": float(poi.latitude), "longitude": float(poi.longitude),
                "duration_min": poi.suggested_duration_min, "opening_hours": poi.opening_hours,
                "confidence": float(item.interest_score or 0.5), "suggestion": item.notes,
            }
            row.candidates = [*row.candidates, candidate]
            candidate_by_poi[poi.id] = candidate
        for node, poi in plan_rows:
            if poi.id in candidate_by_poi:
                continue
            candidate = {
                "id": str(uuid4()), "provider": poi.provider or "local", "provider_id": poi.provider_id or str(poi.id),
                "existing_poi_id": str(poi.id), "in_current_plan": True,
                "name": poi.name, "address": poi.address, "category": poi.category,
                "planning_category": "FOOD" if node.note and "用餐安排" in node.note else "LODGING" if node.note == "住宿安排" else _candidate_planning_category(poi.category, poi.name),
                "meal_type": next((kind for kind, label in (("BREAKFAST", "早餐"), ("LUNCH", "午餐"), ("DINNER", "晚餐"), ("SNACK", "小吃/加餐")) if node.note and label in node.note), None),
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
            "selected_candidate_ids": list(dict.fromkeys([
                *(item["candidate_id"] for item in plan_stops),
                *(
                    candidate_by_poi[poi.id]["id"]
                    for item, poi in existing_places
                    if item.source_type == "ADVISOR"
                    and candidate_by_poi[poi.id].get("planning_category") == "LODGING"
                ),
            ])),
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
    row.context = {**(row.context or {}), "planning_state": planning_state(row)}
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
    return await read_session(db, row)


@router.patch("/sessions/{session_id}/context", response_model=AdvisorSessionRead)
async def update_session_context(
    session_id: UUID,
    payload: AdvisorSessionContextUpdate,
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    row = await owned_session(db, session_id, user.id, lock=True)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    context = dict(row.context or {})
    pending_before_update = pending_agent_question_from_context(context)
    previous_assistant = await db.scalar(select(AdvisorMessageModel).where(
        AdvisorMessageModel.session_id == row.id,
        AdvisorMessageModel.owner_id == user.id,
        AdvisorMessageModel.role == "assistant",
    ).order_by(AdvisorMessageModel.created_at.desc()).limit(1))
    pending_before_update = pending_before_update or pending_agent_question(
        previous_assistant.payload if previous_assistant else None
    )
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
    previous_schedules = context.get("candidate_schedules", {})
    if not isinstance(previous_schedules, dict):
        previous_schedules = {}
    selected_ids = {str(value) for value in context.get("selected_candidate_ids", [])}
    available_candidate_ids = {
        str(candidate.get("id")) for candidate in row.candidates or []
        if isinstance(candidate, dict) and candidate.get("id")
    }
    candidate_schedules: dict[str, dict[str, str]] = {}
    for candidate_id, schedule in previous_schedules.items():
        candidate_id = str(candidate_id)
        if candidate_id not in selected_ids or candidate_id not in available_candidate_ids or not isinstance(schedule, dict):
            continue
        retained = dict(schedule)
        scheduled_day = retained.get("day")
        if scheduled_day:
            try:
                parsed_day = date.fromisoformat(str(scheduled_day))
            except ValueError:
                parsed_day = None
            if parsed_day is None or not row.start_date or not row.end_date or not row.start_date <= parsed_day <= row.end_date:
                retained.pop("day", None)
        if retained:
            candidate_schedules[candidate_id] = retained
    selection_schedule_changed = candidate_schedules != previous_schedules
    if selection_schedule_changed:
        context["candidate_schedules"] = candidate_schedules
    if before != after or selection_schedule_changed:
        context["revision"] = int(context.get("revision", 0)) + 1
    pending_invalidated = bool(before != after and pending_before_update)
    if pending_invalidated:
        context["pending_task_invalidated"] = True
        context.pop("pending_agent_question", None)
    context["slot_sources"] = {
        "destination": "advisor_form", "start_date": "advisor_form", "end_date": "advisor_form",
        "budget": "advisor_form", "pace_level": "advisor_form", "preferences": "advisor_form",
        "origin": "advisor_form", "companions": "advisor_form", "hard_constraints": "advisor_form",
    }
    context["planning_inputs"] = new_planning_inputs
    context["planning_state"] = planning_state(row)
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
        payload={
            "event": "CONTEXT_UPDATED",
            "planning_state": context["planning_state"],
            "invalidated_pending_task": pending_invalidated,
        },
    ))
    await db.commit()
    await db.refresh(row)
    return await read_session(db, row)


@router.get("/sessions", response_model=list[AdvisorSessionRead])
async def list_sessions(db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    rows = (await db.scalars(select(AdvisorSessionModel).where(
        AdvisorSessionModel.owner_id == user.id,
    ).order_by(AdvisorSessionModel.updated_at.desc()))).all()
    profile_memories = await _active_personal_memories(db, user.id)
    return [await read_session(db, row, profile_memories=profile_memories) for row in rows]


@router.get("/sessions/{session_id}", response_model=AdvisorSessionRead)
async def get_session_detail(session_id: UUID, db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    row = await owned_session(db, session_id, user.id, lock=True)
    await refresh_trip_snapshot(db, row)
    await db.commit()
    return await read_session(db, row)


@router.post("/sessions/{session_id}/destinations", response_model=AdvisorSessionRead)
async def recommend_destinations(
    session_id: UUID,
    payload: AdvisorDestinationDiscoveryRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
    maps=Depends(get_maps_provider),
    redis: Redis = Depends(get_rate_limit_redis),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=80),
):
    """Recommend a few cities using bounded, live POI searches as evidence."""
    if not isinstance(payload, AdvisorDestinationDiscoveryRequest):
        payload = AdvisorDestinationDiscoveryRequest()
    started_at = perf_counter()
    row = await owned_session(db, session_id, user.id, lock=True)
    request_key = idempotency_key if isinstance(idempotency_key, str) else None
    if request_key:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", request_key):
            raise HTTPException(422, detail="Idempotency-Key 格式无效")
        existing_response = await db.scalar(select(AdvisorMessageModel.id).where(
            AdvisorMessageModel.owner_id == user.id,
            AdvisorMessageModel.session_id == row.id,
            AdvisorMessageModel.role == "assistant",
            AdvisorMessageModel.payload["event"].as_string() == "DESTINATION_RECOMMENDATIONS",
            AdvisorMessageModel.payload["response_to_idempotency_key"].as_string()
            == request_key,
        ).limit(1))
        if existing_response is not None:
            return await read_session(db, row)
    if row.status != "ACTIVE":
        raise HTTPException(409, detail="已确认的会话不能继续修改")
    if row.destination:
        raise HTTPException(409, detail="当前会话已有目的地")
    if settings.maps_provider != "amap":
        raise HTTPException(503, detail="目的地推荐需要配置真实地图服务；当前模拟地图不能用于城市推荐")
    planning_inputs = (row.context or {}).get("planning_inputs", {})
    origin = str(planning_inputs.get("origin", "")).strip()
    queries = _destination_queries(
        row.preference_text,
        [str(item) for item in planning_inputs.get("hard_constraints", [])],
    )
    has_explicit_interest = bool(queries)
    if not queries:
        # Start with a small, varied shortlist when the user has not expressed a
        # supported interest yet. Results remain map evidence, not a personalized
        # or seasonal ranking; the conversation can refine them afterward.
        queries = [("自然与公园", "公园"), ("地方美食", "美食")]
    approved_cities = [payload.city] if payload.city else list(_DESTINATION_CITIES)

    await enforce_identity_rate_limit(
        redis,
        str(user.id),
        bucket="advisor-destinations",
        limit=settings.advisor_destination_limit_per_user,
        window_seconds=settings.advisor_destination_window_seconds,
    )

    recover_decision = None
    persist_decision = None
    discovery_checkpoint = None
    stage_lifecycle_checkpoint = None
    if request_key:
        context_revision = int((row.context or {}).get("revision", 0))
        recover_decision, persist_decision = readonly_tool_decision_callbacks(
            db, owner_id=user.id, session_id=row.id, task_id=request_key,
            context_revision=context_revision,
        )
        discovery_checkpoint = checkpoint_writer(
            owner_id=user.id, session_id=row.id, task_id=request_key,
            phase="destination_discovery", context_revision=context_revision,
        )
        stage_lifecycle_checkpoint = checkpoint_writer(
            owner_id=user.id, session_id=row.id, task_id=f"destination:{request_key}",
            phase="turn_stage", context_revision=context_revision,
        )
    retrieved_at = datetime.now(UTC).isoformat()
    context = dict(row.context or {})
    context["destination_discovery_city"] = payload.city
    row.context = context
    cached_searches, search_observation_callback = await destination_evidence_callbacks(
        db=db, owner_id=user.id, session_id=row.id, maps=maps,
        task_id=f"destination:{request_key}" if request_key else None,
        context_revision=int((row.context or {}).get("revision", 0)),
        queries=queries, approved_cities=approved_cities,
    )
    discovery, stage_execution = await execute_destination_stage(
        runner=run_destination_discovery,
        runner_kwargs={
            "maps": maps,
            "queries": queries,
            "approved_cities": approved_cities,
            "user_goal": row.preference_text or "探索一个适合我的旅行目的地",
            "api_key": settings.llm_api_key,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "token_budget": min(
            settings.llm_max_tokens_per_turn,
            remaining_session_output_tokens(row.context or {}, settings.llm_max_output_tokens_per_session),
            ),
            "cached_searches": cached_searches,
            "search_observation_callback": search_observation_callback,
            "checkpoint": discovery_checkpoint,
            "load_decision_checkpoint": recover_decision,
            "save_decision_checkpoint": persist_decision,
        },
        checkpoint=stage_lifecycle_checkpoint,
    )
    city_results: dict[str, dict] = {
        city: {"city": city, "matched_interests": [], "places": [], "place_count": 0, "failed_interests": []}
        for city in approved_cities
    }
    for result in discovery.searches:
        city = result["city"]
        label = result["interest"]
        if result["places"]:
            city_results[city]["matched_interests"].append(label)
            city_results[city]["place_count"] += len(result["places"])
            city_results[city]["places"].extend(result["places"])
    failures = 0
    for call in discovery.runtime.calls:
        if call.get("status") == "SUCCEEDED":
            continue
        failures += 1
        call_input = call.get("input_data") if isinstance(call.get("input_data"), dict) else {}
        city = call_input.get("city")
        interest_index = call_input.get("interest_index")
        if city in city_results and isinstance(interest_index, int) and 0 <= interest_index < len(queries):
            label = queries[interest_index][0]
            if label not in city_results[city]["failed_interests"]:
                city_results[city]["failed_interests"].append(label)
    usage = discovery.runtime.execution_metrics
    if usage["input_tokens"] or usage["output_tokens"]:
        context = dict(row.context or {})
        record_session_usage(context, usage["input_tokens"], usage["output_tokens"])
        row.context = context

    retrieved_at = datetime.now(UTC).isoformat()
    ranked = sorted(
        (item for item in city_results.values() if item["places"]),
        key=lambda item: (-len(item["matched_interests"]), -item["place_count"], item["city"]),
    )[:3]
    for item in ranked:
        unique_places = {place["provider_id"]: place for place in item["places"]}
        item["places"] = rank_map_places(list(unique_places.values()))[:4]
        item["reasons"] = [f"地图检索到 {item['place_count']} 个相关地点，覆盖 {len(item['matched_interests'])} 类偏好："
                           + "、".join(item["matched_interests"])]
        if any(place.get("popularity_score") is not None for place in item["places"]):
            item["reasons"].append("地点评分来自地图服务；不代表全网热度")
        item["provider"] = item["places"][0]["provider"]
        item["retrieved_at"] = retrieved_at

    search_count = len(discovery.runtime.calls)
    if discovery.loop.pending_question:
        reply = discovery.loop.pending_question + " 你可以先补充偏好，之后再继续搜索目的地。"
    elif ranked:
        basis = "你提到的兴趣" if has_explicit_interest else "自然与美食两类初步探索"
        reply = f"以下城市按地图对{basis}的检索结果整理。它们是待比较的候选，不代表交通、费用或季节适宜度已核实；选择后仍需确认日期和行程草案。"
        if not row.start_date or not row.end_date:
            reply += "目前没有旅行日期，因此尚未比较季节适配。"
        if not origin:
            reply += "目前没有出发地，因此尚未比较交通时间和交通费用。"
        if failures:
            reply += f"另有 {failures} 项地图搜索暂时失败，未用于候选排序。"
        if search_count < len(approved_cities) * min(2, len(queries)):
            reply += "Agent 根据当前地图证据提前收敛了候选；本次没有覆盖全部城市与偏好组合，排名只代表已检索范围。"
    elif failures:
        reply = f"有 {failures} 项地图搜索暂时失败，且当前没有可展示的核验结果。你的偏好已保留，可以稍后重试或直接填写目的地。"
    elif search_count < len(approved_cities) * min(2, len(queries)):
        reply = "Agent 根据当前地图证据结束了本轮探索，但尚未找到可展示的核验地点，且搜索范围未覆盖全部候选。你可以补充偏好或再次探索。"
    else:
        reply = "地图服务没有找到可核验的匹配地点。你的偏好已保留，可以修改偏好后重试，或直接填写目的地。"
    if not discovery.loop.pending_question:
        context = dict(row.context or {})
        context.pop("destination_discovery_city", None)
        row.context = context
    pending_task_id = uuid4().hex[:32] if discovery.loop.pending_question else None
    message_payload = {
        "event": "DESTINATION_RECOMMENDATIONS",
        **({"response_to_idempotency_key": request_key} if request_key else {}),
        "recommendations": ranked,
        "source": "AMap Web Service",
        "retrieved_at": retrieved_at,
        "search_count": search_count,
        "failed_search_count": failures,
        "search_scope_complete": (
            search_count == len(approved_cities) * min(2, len(queries)) and failures == 0
        ),
        "follow_up_question": discovery.loop.pending_question,
        "agent_task": AdvisorTaskSnapshot(
            task_id=pending_task_id,
            task_type="DISCOVERY",
            status="WAITING_USER",
            goal=f"继续为自由探索推荐目的地。用户原始想法：{row.preference_text[:800]}",
            context_revision=int((row.context or {}).get("revision", 0)),
            turn_number=1,
            answers=[],
            question=discovery.loop.pending_question,
            action_summary=discovery.decision_summary[:240],
        ).model_dump(mode="json") if pending_task_id else None,
        "agent_trace": {
            "status": discovery.loop.status,
            "decision_source": discovery.decision_source,
            "decision_summary": discovery.decision_summary,
            "transitions": discovery.loop.transitions,
            "execution_metrics": discovery.runtime.execution_metrics,
            "stage_statuses": [
                {"stage": stage, "status": stage_status}
                for stage, stage_status in stage_execution["statuses"].items()
            ],
        },
        "stage_execution": {
            "order": stage_execution["execution_order"],
            "statuses": stage_execution["statuses"],
        },
        "comparison_limits": {
            "season_not_compared": not (row.start_date and row.end_date),
            "travel_from_origin_not_compared": not bool(origin),
            "ranking_basis": "map_interest_match_count",
        },
    }
    if discovery.loop.pending_question:
        message_payload.update({
            "pending_task": f"继续为自由探索推荐目的地。用户原始想法：{row.preference_text[:800]}",
            "pending_task_type": "DISCOVERY",
            "pending_task_kind": "DESTINATION_RECOMMENDATION",
            "pending_question": discovery.loop.pending_question,
            "pending_task_id": pending_task_id,
            "pending_turn_number": 1,
            "pending_answers": [],
            "context_revision": int((row.context or {}).get("revision", 0)),
        })
    db.add(AdvisorMessageModel(
        session_id=row.id,
        owner_id=user.id,
        role="assistant",
        content=reply,
        payload=message_payload,
    ))
    db.add(AdvisorRunEventModel(
        owner_id=user.id,
        session_id=row.id,
        phase="tool",
        operation="recommend_destinations",
        status="SUCCEEDED" if ranked else "NO_RESULTS",
        duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        error_code=None,
        metadata_json={"candidate_count": len(ranked), "search_count": search_count,
                       "failed_search_count": failures,
                       "decision_source": discovery.decision_source,
                       "loop_status": discovery.loop.status},
    ))
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return await read_session(db, row)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: UUID, db: AsyncSession = Depends(get_session), user: UserModel = Depends(get_current_user)):
    row = await owned_session(db, session_id, user.id)
    await db.delete(row)
    await db.commit()


from travelpilot_api.api.v1.advisor_messages import messages_router

router.include_router(messages_router)
