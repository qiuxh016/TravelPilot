import json
from datetime import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.engines.impact_analysis.replan import Node, plan_diff, replan
from travelpilot_core.skills import select_skill
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel
from travelpilot_infra.db.repositories.plans import PlanRepository
from travelpilot_infra.db.session import get_session
from travelpilot_worker.jobs import QUEUE, RESULT_PREFIX, ReplanJob

from travelpilot_api.api.v1.plans import to_read
from travelpilot_api.dependencies import get_owned_trip
from travelpilot_api.schemas.events import DiffItem, EventCreate, EventRead, ReplanRequest
from travelpilot_api.settings import settings

router = APIRouter(prefix="/trips/{trip_id}", tags=["events"])


def _snapshots(rows) -> list[Node]:
    return [Node(node.poi_id, poi.name, poi.category, node.day, node.start_time,
                 node.end_time, node.priority, node.status, node.note) for node, poi in rows]


def _validate(nodes: list[Node], pois: dict[UUID, PoiModel]) -> list[str]:
    warnings = []
    days = {node.day for node in nodes}
    for day in days:
        sequence = sorted((node for node in nodes if node.day == day), key=lambda node: node.start_time)
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


@router.post("/events", response_model=EventRead, status_code=201)
async def create_event(trip_id: UUID, payload: EventCreate,
                       session: AsyncSession = Depends(get_session),
                       trip: TripModel = Depends(get_owned_trip)):
    if not trip.start_date <= payload.effective_at.date() <= trip.end_date:
        raise HTTPException(422, detail="Event is outside trip dates")
    event = TripEventModel(trip_id=trip_id, event_type=payload.event_type,
                           effective_at=payload.effective_at, payload=payload.payload)
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.post("/replan-runs")
async def create_replan(trip_id: UUID, payload: ReplanRequest,
                        session: AsyncSession = Depends(get_session),
                        _trip: TripModel = Depends(get_owned_trip)):
    trip = await session.get(TripModel, trip_id)
    event = await session.get(TripEventModel, payload.event_id)
    if trip is None or event is None or event.trip_id != trip_id:
        raise HTTPException(404, detail="Trip or event not found")
    repository = PlanRepository(session)
    previous = (await repository.list_by_trip(trip_id))[:1]
    if not previous:
        raise HTTPException(409, detail="Generate a plan before replanning")
    previous_plan = previous[0]
    old_rows = (await repository.get_with_nodes(trip_id, previous_plan.id))[1]
    old_nodes = _snapshots(old_rows)
    try:
        new_nodes, warnings = replan(old_nodes, event.event_type,
                                     event.effective_at, event.payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if event.event_type == "WEATHER_CHANGE":
        weather_skill = select_skill("weather_replan")
        removed = [node for node in old_nodes if node not in new_nodes]
        wishlist = (await session.execute(select(WishlistItemModel, PoiModel)
                    .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
                    .where(WishlistItemModel.trip_id == trip_id))).all()
        available = [(item, poi) for item, poi in wishlist
                     if poi.category in {"MUSEUM", "RESTAURANT", "CAFE", "INDOOR"}
                     and all(node.poi_id != poi.id for node in old_nodes)]
        for lost in removed:
            match = next(((item, poi) for item, poi in available
                          if poi.suggested_duration_min <=
                          (lost.end_time.hour * 60 + lost.end_time.minute -
                           lost.start_time.hour * 60 - lost.start_time.minute)), None)
            if match is None:
                continue
            item, poi = match
            available.remove(match)
            end = lost.start_time.hour * 60 + lost.start_time.minute + poi.suggested_duration_min
            new_nodes.append(Node(poi.id, poi.name, poi.category, lost.day,
                                  lost.start_time, time(end // 60, end % 60),
                                  item.priority))
            warnings.append(f"雨天替换：{lost.name} → {poi.name}")
    if event.event_type == "USER_ADD":
        poi_id = event.payload.get("poi_id")
        try:
            poi_id = UUID(str(poi_id))
        except ValueError as exc:
            raise HTTPException(422, detail="Invalid poi_id") from exc
        row = (await session.execute(select(WishlistItemModel, PoiModel)
               .join(PoiModel, WishlistItemModel.poi_id == PoiModel.id)
               .where(WishlistItemModel.trip_id == trip_id, WishlistItemModel.poi_id == poi_id))).one_or_none()
        if row is None:
            raise HTTPException(422, detail="POI must be in Wishlist")
        item, poi = row
        if any(node.poi_id == poi_id for node in new_nodes):
            warnings.append(f"地点已在计划中：{poi.name}")
        else:
            day = event.effective_at.date()
            day_nodes = [node for node in new_nodes if node.day == day]
            start = max([event.effective_at.hour * 60 + event.effective_at.minute, 9 * 60] +
                        [node.end_time.hour * 60 + node.end_time.minute + 20 for node in day_nodes])
            end = start + poi.suggested_duration_min
            if end <= 20 * 60 and len(day_nodes) < 6:
                new_nodes.append(Node(poi.id, poi.name, poi.category, day,
                                      time(start // 60, start % 60),
                                      time(end // 60, end % 60), item.priority))
            else:
                warnings.append(f"当天无法加入：{poi.name}")
    pois = {node.poi_id: poi for node, poi in old_rows}
    for node in new_nodes:
        if node.poi_id not in pois:
            pois[node.poi_id] = await session.get(PoiModel, node.poi_id)
    conflicts = _validate(new_nodes, pois)
    changes = plan_diff(old_nodes, new_nodes, event.event_type)
    needs_input = event.event_type == "RESERVATION_CHANGE" and (
        not event.payload.get("start_time") or not event.payload.get("end_time")
    )
    plan = TripPlanModel(trip_id=trip_id, version=await repository.next_version(trip_id),
                         status="PLAN_CONFLICT" if conflicts else "NEEDS_USER_INPUT" if needs_input else "READY",
                         warnings=warnings + conflicts)
    session.add(plan)
    await session.flush()
    session.add_all([TripPlanNodeModel(
        plan_id=plan.id, poi_id=node.poi_id, day=node.day,
        start_time=node.start_time, end_time=node.end_time, priority=node.priority,
        status=node.status, note=node.note,
    ) for node in new_nodes])
    session.add_all([PlanChangeModel(
        plan_id=plan.id, change_type=change["operation"],
        stable_key=change["stable_key"], reason_code=change["reason_code"],
        change_cost=change["change_cost"],
        before_data=change["before"], after_data=change["after"],
        reason=change["reason_code"],
    ) for change in changes if change["operation"] != "KEEP"])
    workflow = WorkflowRunModel(trip_id=trip_id, kind="REPLAN", status="SUCCEEDED",
                                input_data={"event_id": str(event.id), "source_plan_id": str(previous_plan.id),
                                            "skill": ({"name": weather_skill.name, "version": weather_skill.version}
                                                      if event.event_type == "WEATHER_CHANGE" else None)},
                                output_data={"plan_id": str(plan.id), "change_cost": sum(change["change_cost"] for change in changes)})
    session.add(workflow)
    trip.status = plan.status
    await session.commit()
    full = await repository.get_with_nodes(trip_id, plan.id)
    return {"run_id": workflow.id, "plan": to_read(*full), "diff": changes}


@router.post("/replan-jobs", status_code=202)
async def queue_replan(trip_id: UUID, payload: ReplanRequest,
                       session: AsyncSession = Depends(get_session),
                       _trip: TripModel = Depends(get_owned_trip)):
    event = await session.get(TripEventModel, payload.event_id)
    if event is None or event.trip_id != trip_id:
        raise HTTPException(404, detail="Event not found")
    job = ReplanJob(id=uuid4(), trip_id=trip_id, event_id=event.id)
    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.setex(RESULT_PREFIX + str(job.id), 86400, json.dumps({"status": "QUEUED", "trip_id": str(trip_id)}))
        await redis.rpush(QUEUE, job.model_dump_json())
    except RedisError as exc:
        raise HTTPException(503, detail="后台队列暂时不可用") from exc
    finally:
        await redis.aclose()
    return {"job_id": job.id, "status": "QUEUED"}


@router.get("/replan-jobs/{job_id}")
async def get_replan_job(trip_id: UUID, job_id: UUID,
                         session: AsyncSession = Depends(get_session),
                         _trip: TripModel = Depends(get_owned_trip)):
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
async def get_diff(trip_id: UUID, plan_id: UUID,
                   session: AsyncSession = Depends(get_session),
                   _trip: TripModel = Depends(get_owned_trip)):
    repository = PlanRepository(session)
    current = await repository.get_with_nodes(trip_id, plan_id)
    if current is None:
        raise HTTPException(404, detail="Plan not found")
    plan = current[0]
    previous = await session.scalar(select(TripPlanModel).where(
        TripPlanModel.trip_id == trip_id, TripPlanModel.version == plan.version - 1))
    if previous is None:
        return []
    old = await repository.get_with_nodes(trip_id, previous.id)
    diff = plan_diff(_snapshots(old[1]), _snapshots(current[1]), "REPLAN")
    saved = (await session.execute(select(PlanChangeModel).where(
        PlanChangeModel.plan_id == plan_id))).scalars().all()
    by_key = {(change.stable_key, change.change_type): change for change in saved}
    for change in diff:
        persisted = by_key.get((change["stable_key"], change["operation"]))
        if persisted is not None:
            change["reason_code"] = persisted.reason_code
            change["change_cost"] = persisted.change_cost
    return diff
