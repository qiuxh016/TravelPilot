import json
from datetime import UTC, datetime, time, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.engines.impact_analysis.insertion import (
    UserAddCandidate,
    find_user_add_slot,
)
from travelpilot_core.engines.impact_analysis.replacements import (
    ReplanCandidate,
    search_weather_replacements,
)
from travelpilot_core.engines.impact_analysis.replan import (
    Node,
    plan_diff,
    replan,
    trusted_environment_profile,
)
from travelpilot_core.skills import check_skill_applicability, load_skill
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel
from travelpilot_infra.db.repositories.plans import PlanRepository
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError
from travelpilot_worker.jobs import QUEUE, RESULT_PREFIX, ReplanJob

from travelpilot_api.api.v1.plans import to_read
from travelpilot_api.dependencies import get_maps_provider, get_owned_trip
from travelpilot_api.schemas.events import (
    DiffItem,
    EventCreate,
    EventRead,
    ReplanConfirm,
    ReplanConfirmRead,
    ReplanPreviewRead,
    ReplanRequest,
)
from travelpilot_api.settings import settings

router = APIRouter(prefix="/trips/{trip_id}", tags=["events"])

_REPLAN_RELAXATIONS = {
    "DAILY_CAPACITY": "确认是否可减少一个低优先级地点，或允许当天增加一个地点。",
    "OPENING_HOURS_UNKNOWN": "补充候选地点的营业时间后重新规划。",
    "CLOSED_AT_SLOT": "确认是否可调整该时段或移动一个未预约地点。",
    "ROUTE_TIME_UNKNOWN": "检查地图服务配置或稍后重试路线核验。",
    "TIME_OR_ROUTE_CONFLICT": "确认是否可缩短换乘缓冲、移动未预约地点，或选择更近的候选。",
    "WEATHER_PROFILE": "确认候选地点的室内外属性和当前天气敏感度。",
    "INVALID_DURATION": "补充候选地点的合理游览时长。",
    "NO_CANDIDATES": "向本次旅行收藏添加其他备选地点。",
    "CANDIDATE_CONFLICT": "添加另一个备选地点，或确认哪一个受影响时段优先。",
    "OBJECTIVE_TRADEOFF": "确认是否愿意接受较高绕行或计划改动成本以填补该时段。",
}


def _effective_local_datetime(event: TripEventModel) -> datetime:
    """Restore the submitted wall-clock offset after PostgreSQL round-trips it."""
    offset_minutes = getattr(event, "effective_timezone_offset_minutes", 480)
    return event.effective_at.astimezone(timezone(timedelta(minutes=offset_minutes)))


def _relaxation_suggestion(reason: str) -> str:
    return _REPLAN_RELAXATIONS.get(reason, "检查行程约束后重试，或手动调整该时段。")


def _snapshots(rows, environment_profiles: dict[UUID, dict | None] | None = None) -> list[Node]:
    environment_profiles = environment_profiles or {}
    return [
        Node(
            node.poi_id,
            poi.name,
            poi.category,
            node.day,
            node.start_time,
            node.end_time,
            node.priority,
            node.status,
            node.note,
            environment_profiles.get(node.poi_id) or poi.environment_profile,
        )
        for node, poi in rows
    ]


def _validate(nodes: list[Node], pois: dict[UUID, PoiModel]) -> list[str]:
    warnings = []
    days = {node.day for node in nodes}
    for day in days:
        sequence = sorted(
            (node for node in nodes if node.day == day), key=lambda node: node.start_time
        )
        if len(sequence) > 6:
            warnings.append(f"{day} 超过每日 6 个地点")
        previous_end = time(9)
        for node in sequence:
            if node.start_time < previous_end or node.end_time > time(20):
                warnings.append(f"{node.name} 时间冲突或未保留缓冲")
            hours = pois[node.poi_id].opening_hours or {}
            if hours.get("open") and node.start_time < time.fromisoformat(hours["open"]):
                warnings.append(f"{node.name} 尚未营业")
            if hours.get("close") and node.end_time > time.fromisoformat(hours["close"]):
                warnings.append(f"{node.name} 超出营业时间")
            previous_end = node.end_time
    return warnings


def _safe_weather_alternative(item: WishlistItemModel, poi: PoiModel, condition: str) -> bool:
    profile = item.environment_profile or poi.environment_profile or {}
    if not trusted_environment_profile(profile):
        return False
    exposure = str(profile.get("exposure", "UNKNOWN")).upper()
    sensitivity = profile.get("sensitivity", {})
    if isinstance(sensitivity, dict):
        sensitivity = sensitivity.get(condition.upper(), "UNKNOWN")
    sensitivity = str(sensitivity).upper()
    return exposure == "INDOOR" or (exposure in {"OUTDOOR", "MIXED"} and sensitivity == "LOW")


@router.post("/events", response_model=EventRead, status_code=201)
async def create_event(
    trip_id: UUID,
    payload: EventCreate,
    session: AsyncSession = Depends(get_session),
    trip: TripModel = Depends(get_owned_trip),
):
    if not trip.start_date <= payload.effective_at.date() <= trip.end_date:
        raise HTTPException(422, detail="Event is outside trip dates")
    event = TripEventModel(
        trip_id=trip_id,
        event_type=payload.event_type,
        effective_at=payload.effective_at,
        effective_timezone_offset_minutes=int(payload.effective_at.utcoffset().total_seconds() // 60),
        payload=payload.payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.post("/replan-runs", response_model=ReplanPreviewRead)
async def create_replan(
    trip_id: UUID,
    payload: ReplanRequest,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
):
    trip = await session.get(TripModel, trip_id)
    event = await session.get(TripEventModel, payload.event_id)
    if trip is None or event is None or event.trip_id != trip_id:
        raise HTTPException(404, detail="Trip or event not found")
    effective_at = _effective_local_datetime(event)
    repository = PlanRepository(session)
    previous = (await repository.list_by_trip(trip_id))[:1]
    if not previous:
        raise HTTPException(409, detail="Generate a plan before replanning")
    previous_plan = previous[0]
    old_rows = (await repository.get_with_nodes(trip_id, previous_plan.id))[1]
    environment_profiles = dict(
        (
            await session.execute(
                select(WishlistItemModel.poi_id, WishlistItemModel.environment_profile).where(
                    WishlistItemModel.trip_id == trip_id
                )
            )
        ).all()
    )
    old_nodes = _snapshots(old_rows, environment_profiles)
    if event.event_type == "USER_ADD":
        # This event needs an owner-scoped Wishlist/POI lookup and route-aware slot
        # search; the pure replan engine cannot safely synthesize those inputs.
        new_nodes, warnings = list(old_nodes), []
    else:
        try:
            new_nodes, warnings = replan(old_nodes, event.event_type, effective_at, event.payload)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
    search_diagnostics = None
    skill_decision = None
    weather_skill = None
    if event.event_type == "WEATHER_CHANGE":
        weather_skill_candidate = load_skill("weather-replan")
        applicability = check_skill_applicability(
            weather_skill_candidate,
            {
                "task": "weather_replan",
                "verified_weather": False,
                "simulated_weather": True,
            },
        )
        weather_skill = weather_skill_candidate if applicability.applicable else None
        skill_decision = {
            "name": weather_skill_candidate.name,
            "version": weather_skill_candidate.version,
            "selected": applicability.applicable,
            "reason_code": applicability.reason_code,
            "missing_requirements": list(applicability.missing_requirements),
            "matched_exclusions": list(applicability.matched_exclusions),
        }
        removed = [node for node in old_nodes if node not in new_nodes]
        wishlist = (
            await session.execute(
                select(WishlistItemModel, PoiModel)
                .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
                .where(WishlistItemModel.trip_id == trip_id)
            )
        ).all()
        condition = str(event.payload.get("condition", "RAIN")).upper()
        available = sorted(
            [
                (item, poi)
                for item, poi in wishlist
                if _safe_weather_alternative(item, poi, condition)
                and all(node.poi_id != poi.id for node in old_nodes)
            ],
            key=lambda pair: (
                -pair[0].priority,
                -(float(pair[0].interest_score or 0)),
                pair[1].name,
            ),
        )
        max_candidates = 8
        if len(available) > max_candidates:
            warnings.append(f"候选地点超过 {max_candidates} 个，仅评估优先级最高的候选")
            available = available[:max_candidates]
        candidates = [
            ReplanCandidate(
                poi.id,
                poi.name,
                poi.category,
                float(poi.latitude),
                float(poi.longitude),
                poi.suggested_duration_min,
                item.priority,
                float(item.interest_score or 0),
                poi.opening_hours,
                item.environment_profile or poi.environment_profile,
            )
            for item, poi in available
        ]
        affected_days = {node.day for node in removed}
        route_ids = list(
            dict.fromkeys(
                [node.poi_id for node in new_nodes if node.day in affected_days]
                + [candidate.poi_id for candidate in candidates]
            )
        )
        poi_by_id = {poi.id: poi for _, poi in old_rows}
        poi_by_id.update({poi.id: poi for _, poi in available})
        travel_minutes: dict[tuple[UUID, UUID], int] = {}
        maps = None
        needs_route = len(removed) > 1 or any(node.day in affected_days for node in new_nodes)
        if needs_route and len(route_ids) > 1:
            try:
                maps = get_maps_provider()
                matrix = await maps.get_distance_matrix(
                    [
                        (float(poi_by_id[poi_id].latitude), float(poi_by_id[poi_id].longitude))
                        for poi_id in route_ids
                    ]
                )
                if len(matrix) != len(route_ids) or any(
                    len(row) != len(route_ids) for row in matrix
                ):
                    raise ValueError("invalid distance matrix dimensions")
                if any(value < 0 for row in matrix for value in row):
                    raise ValueError("negative route duration")
                travel_minutes = {
                    (origin, destination): matrix[i][j]
                    for i, origin in enumerate(route_ids)
                    for j, destination in enumerate(route_ids)
                }
            except (HTTPException, MapsProviderError, ValueError, TypeError):
                warnings.append("地图路线耗时不可用；无法验证替代地点的交通可达性")
        provider = getattr(maps, "provider", maps)
        simulated_routes = (
            provider is not None and provider.__class__.__name__ == "FakeMapsProvider"
        )
        search = search_weather_replacements(
            removed,
            new_nodes,
            candidates,
            travel_minutes,
            condition=condition,
        )
        assignments = search.assignments
        search_diagnostics = {
            "objective": search.objective,
            "objective_weights": search.objective_weights,
            "beam_width": search.beam_width,
            "states_pruned": search.states_pruned,
            "peak_frontier_size": search.peak_frontier_size,
            "diagnostics": list(search.diagnostics),
            "assignments": [
                {
                    "removed_poi_id": str(lost.poi_id),
                    "replacement_poi_id": str(replacement.poi_id) if replacement else None,
                    "reason_code": reason,
                    "suggestion": _relaxation_suggestion(reason) if replacement is None else None,
                }
                for lost, replacement, reason in assignments
            ],
        }
        inserted_replacement = False
        for lost, replacement, reason in assignments:
            if replacement is None:
                reason_messages = {
                    "DAILY_CAPACITY": "每日地点数量已达上限",
                    "OPENING_HOURS_UNKNOWN": "候选地点营业时间未知或不完整",
                    "CLOSED_AT_SLOT": "候选地点在该时段未营业",
                    "ROUTE_TIME_UNKNOWN": "候选地点缺少路线耗时",
                    "TIME_OR_ROUTE_CONFLICT": "交通耗时或缓冲时间无法满足",
                    "WEATHER_PROFILE": "候选地点天气属性未核实",
                    "INVALID_DURATION": "候选地点停留时长无效",
                    "NO_CANDIDATES": "没有符合条件的收藏地点",
                    "CANDIDATE_CONFLICT": "候选地点已分配给另一处受影响时段",
                    "OBJECTIVE_TRADEOFF": "替换带来的绕行和计划改动成本高于保留空档",
                }
                warnings.append(
                    f"未替换 {lost.name}：{reason_messages.get(reason, '没有可行候选')}"
                )
                warnings.append(f"建议：{_relaxation_suggestion(reason)}")
                continue
            new_nodes.append(replacement)
            candidates = [
                candidate for candidate in candidates if candidate.poi_id != replacement.poi_id
            ]
            inserted_replacement = True
            warnings.append(f"天气替换：{lost.name} → {replacement.name}（保留原有后续时段）")
        if inserted_replacement and simulated_routes:
            warnings.append("替代地点路线耗时来自本地模拟地图，尚未核验真实交通")
    if event.event_type == "USER_ADD":
        if not trip.start_date <= effective_at.date() <= trip.end_date:
            raise HTTPException(422, detail="Event is outside trip dates")
        poi_id = event.payload.get("poi_id")
        try:
            poi_id = UUID(str(poi_id))
        except ValueError as exc:
            raise HTTPException(422, detail="Invalid poi_id") from exc
        row = (
            await session.execute(
                select(WishlistItemModel, PoiModel)
                .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
                .where(WishlistItemModel.trip_id == trip_id, WishlistItemModel.poi_id == poi_id)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(422, detail="POI must be in Wishlist")
        item, poi = row
        user_add_diagnostic = {
            "poi_id": str(poi_id),
            "outcome": "REJECTED",
            "reason_code": "TIME_OR_ROUTE_CONFLICT",
        }
        if any(node.poi_id == poi_id for node in new_nodes):
            warnings.append(f"地点已在计划中：{poi.name}")
            user_add_diagnostic.update(outcome="UNCHANGED", reason_code="ALREADY_PLANNED")
        else:
            day = effective_at.date()
            day_nodes = sorted(
                (node for node in new_nodes if node.day == day),
                key=lambda node: (node.start_time, node.stable_key),
            )
            add_candidate = UserAddCandidate(
                poi.id,
                poi.name,
                poi.category,
                poi.suggested_duration_min,
                item.priority,
                event.payload.get("opening_hours") or poi.opening_hours,
            )
            candidate, reason = find_user_add_slot(
                new_nodes, add_candidate, effective_at, {}
            )
            travel_minutes = {}
            if day_nodes and reason == "ROUTE_TIME_UNKNOWN":
                try:
                    maps = get_maps_provider()
                    day_poi_ids = {node.poi_id for node in day_nodes}
                    route_pois = [
                        poi,
                        *(existing_poi for _, existing_poi in old_rows
                          if existing_poi.id in day_poi_ids),
                    ]
                    route_pois = list({route_poi.id: route_poi for route_poi in route_pois}.values())
                    poi_positions = {route_poi.id: index for index, route_poi in enumerate(route_pois)}
                    matrix = await maps.get_distance_matrix([
                        (float(route_poi.latitude), float(route_poi.longitude))
                        for route_poi in route_pois
                    ])
                    valid_matrix = (
                        len(matrix) == len(route_pois)
                        and all(len(row_values) == len(route_pois) for row_values in matrix)
                        and all(
                            isinstance(value, int) and not isinstance(value, bool) and value >= 0
                            for row_values in matrix for value in row_values
                        )
                    )
                    if valid_matrix:
                        travel_minutes = {
                            (origin, destination): matrix[poi_positions[origin]][poi_positions[destination]]
                            for origin in poi_positions
                            for destination in poi_positions
                        }
                except (HTTPException, MapsProviderError, ValueError, TypeError):
                    travel_minutes = {}
                candidate, reason = find_user_add_slot(
                    new_nodes, add_candidate, effective_at, travel_minutes
                )
            user_add_diagnostic.update(
                outcome="ADDED" if candidate is not None else "REJECTED",
                reason_code=reason,
            )
            if candidate is not None:
                new_nodes.append(candidate)
            else:
                reason_messages = {
                    "DAILY_CAPACITY": "当天地点数量已达上限",
                    "INVALID_DURATION": "地点游览时长无效",
                    "OPENING_HOURS_UNKNOWN": "地点营业时间未核实，请补充后重试",
                    "CLOSED_AT_SLOT": "候选地点在可用时段未营业",
                    "ROUTE_TIME_UNKNOWN": "路线耗时不可用，无法确认交通时间",
                    "TIME_OR_ROUTE_CONFLICT": "现有预约、交通时间或换乘缓冲导致当天没有可用时段",
                }
                warnings.append(
                    f"当天无法加入：{poi.name}（{reason_messages.get(reason, '没有可用时段')}）"
                )
    else:
        user_add_diagnostic = None
    pois = {node.poi_id: poi for node, poi in old_rows}
    for node in new_nodes:
        if node.poi_id not in pois:
            pois[node.poi_id] = await session.get(PoiModel, node.poi_id)
    conflicts = _validate(new_nodes, pois)
    changes = plan_diff(old_nodes, new_nodes, event.event_type)
    needs_input = event.event_type == "RESERVATION_CHANGE" and (
        not event.payload.get("start_time") or not event.payload.get("end_time")
    )
    user_add_infeasible = event.event_type == "USER_ADD" and user_add_diagnostic["outcome"] != "ADDED"
    proposed_status = (
        "PLAN_CONFLICT" if conflicts
        else "NEEDS_USER_INPUT" if needs_input
        else "INFEASIBLE" if user_add_infeasible
        else "READY"
    )
    preview_nodes = [
        {
            "poi_id": str(node.poi_id), "name": pois[node.poi_id].name,
            "day": node.day.isoformat(), "start_time": node.start_time.isoformat(),
            "end_time": node.end_time.isoformat(), "priority": node.priority,
            "status": node.status, "note": node.note,
        }
        for node in new_nodes
    ]
    workflow = WorkflowRunModel(
        trip_id=trip_id,
        kind="REPLAN",
        status="PREVIEWED",
        input_data={
            "event_id": str(event.id),
            "source_plan_id": str(previous_plan.id),
            "source_plan_version": previous_plan.version,
            "skill": (
                {"name": weather_skill.name, "version": weather_skill.version}
                if weather_skill
                else None
            ),
            "skill_applicability": skill_decision,
        },
        output_data={
            "preview": {
                "status": proposed_status,
                "warnings": warnings + conflicts,
                "nodes": preview_nodes,
            },
            "diff": changes,
            "change_cost": sum(change["change_cost"] for change in changes),
            "replacement_search": search_diagnostics,
            "user_add": user_add_diagnostic,
        },
    )
    session.add(workflow)
    await session.flush()
    await session.commit()
    return {
        "run_id": workflow.id,
        "event_id": event.id,
        "based_on_plan_id": previous_plan.id,
        "based_on_plan_version": previous_plan.version,
        "proposed_version": previous_plan.version + 1,
        "status": "PREVIEWED",
        "proposed_plan_status": proposed_status,
        "warnings": warnings + conflicts,
        "nodes": preview_nodes,
        "diff": changes,
        "created_at": workflow.created_at,
        "expires_at": workflow.created_at + timedelta(minutes=15),
    }


@router.post("/replan-runs/{run_id}/confirm", response_model=ReplanConfirmRead)
async def confirm_replan(
    trip_id: UUID,
    run_id: UUID,
    payload: ReplanConfirm,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
):
    run = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.id == run_id,
            WorkflowRunModel.trip_id == trip_id,
            WorkflowRunModel.kind == "REPLAN",
        )
        .with_for_update()
    )
    if run is None or run.trip_id != trip_id or run.kind != "REPLAN":
        raise HTTPException(404, detail="Replan preview not found")
    output = run.output_data if isinstance(run.output_data, dict) else {}
    preview = output.get("preview") if isinstance(output.get("preview"), dict) else {}
    if run.status == "CONFIRMED":
        plan_id = (output.get("plan_id") if isinstance(output, dict) else None)
        if not plan_id:
            raise HTTPException(409, detail="Confirmed replan is missing its plan reference")
        full = await PlanRepository(session).get_with_nodes(trip_id, UUID(str(plan_id)))
        if full is None:
            raise HTTPException(404, detail="Confirmed plan not found")
        return {"run_id": run.id, "plan": to_read(*full), "diff": output.get("diff", [])}
    if run.status != "PREVIEWED":
        raise HTTPException(409, detail="Replan preview is no longer available")
    if preview.get("status") != "READY":
        raise HTTPException(409, detail="This preview has unresolved conflicts or needs more information")
    input_data = run.input_data if isinstance(run.input_data, dict) else {}
    if (
        input_data.get("source_plan_id") != str(payload.based_on_plan_id)
        or input_data.get("source_plan_version") != payload.based_on_plan_version
    ):
        raise HTTPException(409, detail="Confirmation does not match the plan version used for this preview")
    created_at = run.created_at
    if created_at is None:
        raise HTTPException(409, detail="Replan preview timestamp is unavailable; generate a fresh preview")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created_at > timedelta(minutes=15):
        run.status = "EXPIRED"
        await session.commit()
        raise HTTPException(409, detail="Replan preview expired; generate a fresh preview")

    repository = PlanRepository(session)
    next_version = await repository.next_version(trip_id)
    latest = (await repository.list_by_trip(trip_id))[:1]
    if not latest or latest[0].id != payload.based_on_plan_id or latest[0].version != payload.based_on_plan_version:
        run.status = "STALE"
        await session.commit()
        raise HTTPException(409, detail="Trip plan changed after this preview; generate a fresh preview")
    source_plan = latest[0]
    old_plan_and_rows = await repository.get_with_nodes(trip_id, source_plan.id)
    if old_plan_and_rows is None:
        raise HTTPException(404, detail="Source plan not found")
    old_rows = old_plan_and_rows[1]
    old_environment = dict(
        (await session.execute(
            select(WishlistItemModel.poi_id, WishlistItemModel.environment_profile)
            .where(WishlistItemModel.trip_id == trip_id)
        )).all()
    )
    old_nodes = _snapshots(old_rows, old_environment)
    proposed_rows = preview.get("nodes")
    if not isinstance(proposed_rows, list) or len(proposed_rows) > 100:
        raise HTTPException(409, detail="Replan preview is malformed")
    new_nodes = []
    pois: dict[UUID, PoiModel] = {}
    for item in proposed_rows:
        if not isinstance(item, dict):
            raise HTTPException(409, detail="Replan preview is malformed")
        try:
            poi_id = UUID(str(item["poi_id"]))
            node = Node(
                poi_id=poi_id, name=str(item["name"]), category="",
                day=datetime.fromisoformat(item["day"]).date(),
                start_time=time.fromisoformat(item["start_time"]),
                end_time=time.fromisoformat(item["end_time"]),
                priority=int(item["priority"]), status=str(item["status"]),
                note=item.get("note"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, detail="Replan preview is malformed") from exc
        poi = await session.get(PoiModel, poi_id)
        if poi is None or poi.name != node.name:
            raise HTTPException(409, detail="A proposed place changed or is no longer available")
        if not any(existing.poi_id == poi_id for existing in old_nodes):
            membership = await session.execute(
                select(WishlistItemModel.id).where(
                    WishlistItemModel.trip_id == trip_id,
                    WishlistItemModel.poi_id == poi_id,
                )
            )
            if membership.scalar_one_or_none() is None:
                raise HTTPException(409, detail="A proposed new place is no longer in this trip's Wishlist")
        new_nodes.append(node)
        pois[poi_id] = poi
    conflicts = _validate(new_nodes, pois)
    if conflicts:
        raise HTTPException(409, detail="Preview no longer passes plan validation")
    event_id = UUID(str(input_data.get("event_id", "")))
    event = await session.get(TripEventModel, event_id)
    if event is None or event.trip_id != trip_id:
        raise HTTPException(409, detail="Source event is no longer available")
    changes = plan_diff(old_nodes, new_nodes, event.event_type)
    warnings = preview.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise HTTPException(409, detail="Replan preview is malformed")
    needs_input = event.event_type == "RESERVATION_CHANGE" and (
        not event.payload.get("start_time") or not event.payload.get("end_time")
    )
    status = "NEEDS_USER_INPUT" if needs_input else str(preview.get("status", "READY"))
    plan = TripPlanModel(
        trip_id=trip_id, version=next_version,
        status=status, warnings=warnings,
    )
    session.add(plan)
    await session.flush()
    session.add_all([
        TripPlanNodeModel(
            plan_id=plan.id, poi_id=node.poi_id, day=node.day,
            start_time=node.start_time, end_time=node.end_time,
            priority=node.priority, status=node.status, note=node.note,
        ) for node in new_nodes
    ])
    session.add_all([
        PlanChangeModel(
            plan_id=plan.id, change_type=change["operation"],
            stable_key=change["stable_key"], reason_code=change["reason_code"],
            change_cost=change["change_cost"], before_data=change["before"],
            after_data=change["after"], reason=change["reason_code"],
        ) for change in changes if change["operation"] != "KEEP"
    ])
    trip = await session.get(TripModel, trip_id)
    if trip is not None:
        trip.status = plan.status
    run.status = "CONFIRMED"
    run.output_data = {**output, "plan_id": str(plan.id), "diff": changes}
    await session.commit()
    full = await repository.get_with_nodes(trip_id, plan.id)
    return {"run_id": run.id, "plan": to_read(*full), "diff": changes}


@router.post("/replan-jobs", status_code=202)
async def queue_replan(
    trip_id: UUID,
    payload: ReplanRequest,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
):
    event = await session.get(TripEventModel, payload.event_id)
    if event is None or event.trip_id != trip_id:
        raise HTTPException(404, detail="Event not found")
    job = ReplanJob(id=uuid4(), trip_id=trip_id, event_id=event.id)
    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.setex(
            RESULT_PREFIX + str(job.id),
            86400,
            json.dumps({"status": "QUEUED", "trip_id": str(trip_id)}),
        )
        await redis.rpush(QUEUE, job.model_dump_json())
    except RedisError as exc:
        raise HTTPException(503, detail="后台队列暂时不可用") from exc
    finally:
        await redis.aclose()
    return {"job_id": job.id, "status": "QUEUED"}


@router.get("/replan-jobs/{job_id}")
async def get_replan_job(
    trip_id: UUID,
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
):
    redis = Redis.from_url(settings.redis_url)
    try:
        value = await redis.get(RESULT_PREFIX + str(job_id))
    except RedisError as exc:
        raise HTTPException(503, detail="后台队列暂时不可用") from exc
    finally:
        await redis.aclose()
    if value is None:
        raise HTTPException(404, detail="Job not found")
    result = json.loads(value)
    if result.get("trip_id") != str(trip_id):
        raise HTTPException(404, detail="Job not found")
    return {"job_id": job_id, **result}


@router.get("/plans/{plan_id}/diff", response_model=list[DiffItem])
async def get_diff(
    trip_id: UUID,
    plan_id: UUID,
    session: AsyncSession = Depends(get_session),
    _trip: TripModel = Depends(get_owned_trip),
):
    repository = PlanRepository(session)
    current = await repository.get_with_nodes(trip_id, plan_id)
    if current is None:
        raise HTTPException(404, detail="Plan not found")
    plan = current[0]
    previous = await session.scalar(
        select(TripPlanModel).where(
            TripPlanModel.trip_id == trip_id, TripPlanModel.version == plan.version - 1
        )
    )
    if previous is None:
        return []
    old = await repository.get_with_nodes(trip_id, previous.id)
    diff = plan_diff(_snapshots(old[1]), _snapshots(current[1]), "REPLAN")
    saved = (
        (await session.execute(select(PlanChangeModel).where(PlanChangeModel.plan_id == plan_id)))
        .scalars()
        .all()
    )
    by_key = {(change.stable_key, change.change_type): change for change in saved}
    for change in diff:
        persisted = by_key.get((change["stable_key"], change["operation"]))
        if persisted is not None:
            change["reason_code"] = persisted.reason_code
            change["change_cost"] = persisted.change_cost
    return diff
