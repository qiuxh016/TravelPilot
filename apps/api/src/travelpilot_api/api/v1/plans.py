from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_api.schemas.plans import PlanRead, PlanNodeRead, PlanNodeUpdate, PlanChangeRead
from travelpilot_core.application.plans import PlanService
from travelpilot_infra.db.session import get_session

router = APIRouter(prefix="/trips/{trip_id}", tags=["plans"])


def to_read(plan, rows):
    return PlanRead(
        id=plan.id, trip_id=plan.trip_id, version=plan.version,
        status=plan.status, warnings=plan.warnings, created_at=plan.created_at,
        nodes=[PlanNodeRead(
            id=node.id, poi_id=node.poi_id, name=poi.name, day=node.day,
            start_time=node.start_time, end_time=node.end_time,
            priority=node.priority, status=node.status, note=node.note,
        ) for node, poi in rows],
    )


@router.post("/plan-runs", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
async def generate_plan(trip_id: UUID, session: AsyncSession = Depends(get_session)):
    try:
        plan = await PlanService(session).generate(trip_id)
    except LookupError as exc:
        raise HTTPException(404, detail="Trip not found") from exc
    except ValueError as exc:
        if str(exc) == "WISHLIST_EMPTY":
            raise HTTPException(400, detail={"code": "WISHLIST_EMPTY", "message": "Wishlist is empty"}) from exc
        raise
    full = await PlanService(session).get(trip_id, plan.id)
    return to_read(*full)


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(trip_id: UUID, session: AsyncSession = Depends(get_session)):
    service = PlanService(session)
    plans = await service.list(trip_id)
    return [to_read(plan, (await service.get(trip_id, plan.id))[1]) for plan in plans]


@router.get("/plans/{plan_id}", response_model=PlanRead)
async def get_plan(trip_id: UUID, plan_id: UUID, session: AsyncSession = Depends(get_session)):
    result = await PlanService(session).get(trip_id, plan_id)
    if result is None:
        raise HTTPException(404, detail="Plan not found")
    return to_read(*result)


@router.patch("/plans/{plan_id}/nodes/{node_id}", response_model=PlanRead)
async def update_node(trip_id: UUID, plan_id: UUID, node_id: UUID, payload: PlanNodeUpdate, reason: str | None = None, session: AsyncSession = Depends(get_session)):
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    try:
        node = await service.update_node(plan_id, node_id, payload.model_dump(exclude_unset=True), reason)
    except Exception:
        await session.rollback()
        raise
    if node is None:
        raise HTTPException(404, detail="Plan node not found")
    return to_read(*(await service.get(trip_id, plan_id)))


@router.delete("/plans/{plan_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(trip_id: UUID, plan_id: UUID, node_id: UUID, reason: str | None = None, session: AsyncSession = Depends(get_session)):
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    if not await service.delete_node(plan_id, node_id, reason):
        raise HTTPException(404, detail="Plan node not found")


@router.get("/plans/{plan_id}/changes", response_model=list[PlanChangeRead])
async def list_changes(trip_id: UUID, plan_id: UUID, session: AsyncSession = Depends(get_session)):
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    return await service.changes(plan_id)
