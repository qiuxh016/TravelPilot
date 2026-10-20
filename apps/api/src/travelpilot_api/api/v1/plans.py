from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.plans import PlanService
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import get_session
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.auth import get_current_user
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.schemas.plans import (
    PlanChangeRead,
    PlanNodeRead,
    PlanNodeUpdate,
    PlanRead,
    PlanRunRequest,
)
from travelpilot_api.trip_access import TripAccess, get_trip_access

router = APIRouter(prefix="/trips/{trip_id}", tags=["plans"])


def to_read(plan, rows):
    return PlanRead(
        id=plan.id, trip_id=plan.trip_id, version=plan.version,
        status=plan.status, warnings=plan.warnings, created_at=plan.created_at,
        base_version=plan.base_version,
        created_by_user_id=plan.created_by_user_id,
        created_by_display_name=plan.created_by_display_name,
        change_reason=plan.change_reason,
        nodes=[PlanNodeRead(
            id=node.id, poi_id=node.poi_id, name=poi.name, day=node.day,
            start_time=node.start_time, end_time=node.end_time,
            priority=node.priority, status=node.status, note=node.note,
        ) for node, poi in rows],
    )


async def _access(session: AsyncSession, trip_id: UUID, user: UserModel,
                  role: str = "VIEWER", lock: bool = False) -> TripAccess:
    return await get_trip_access(
        session, trip_id=trip_id, user_id=user.id,
        required_role=role, lock=lock,
    )


async def _version_conflict(session: AsyncSession, trip_id: UUID, service: PlanService,
                            error: ValueError) -> HTTPException:
    latest_version = await service.repository.next_version(trip_id) - 1
    latest = (await service.list(trip_id))
    latest_plan = latest[0] if latest else None
    changes = await service.changes(latest_plan.id) if latest_plan else []
    diff = [{
        "change_type": row.change_type,
        "stable_key": row.stable_key,
        "reason": row.reason,
        "actor_display_name": row.actor_display_name,
    } for row in changes]
    return HTTPException(
        status_code=409,
        detail={
            "code": "PLAN_VERSION_CONFLICT",
            "message": "行程已被其他成员更新，请查看最新版本后再提交。",
            "latest_version": latest_version,
            "latest_plan_id": str(latest_plan.id) if latest_plan else None,
            "latest_changes": diff,
        },
    )


def _actor_name(user: UserModel) -> str:
    return user.display_name or user.username or "旅行成员"


@router.post("/plan-runs", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
async def generate_plan(
    trip_id: UUID,
    payload: PlanRunRequest | None = None,
    session: AsyncSession = Depends(get_session),
    maps=Depends(get_maps_provider),
    user: UserModel = Depends(get_current_user),
):
    await _access(session, trip_id, user, "EDITOR")
    service = PlanService(session)
    try:
        plan = await service.generate(
            trip_id, maps,
            expected_base_version=payload.expected_base_version if payload else None,
            actor_user_id=user.id,
            actor_display_name=_actor_name(user),
            reason=payload.reason if payload else None,
        )
    except LookupError as exc:
        raise HTTPException(404, detail="Trip not found") from exc
    except ValueError as exc:
        if str(exc) == "WISHLIST_EMPTY":
            raise HTTPException(400, detail={"code": "WISHLIST_EMPTY", "message": "Wishlist is empty"}) from exc
        if str(exc).startswith("PLAN_VERSION_CONFLICT:"):
            raise await _version_conflict(session, trip_id, service, exc) from exc
        raise
    except MapsProviderError as exc:
        await session.rollback()
        raise HTTPException(503, detail="地图服务暂时不可用") from exc
    full = await service.get(trip_id, plan.id)
    return to_read(*full)


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(trip_id: UUID, session: AsyncSession = Depends(get_session),
                     user: UserModel = Depends(get_current_user)):
    await _access(session, trip_id, user)
    service = PlanService(session)
    plans = await service.list(trip_id)
    return [to_read(plan, (await service.get(trip_id, plan.id))[1]) for plan in plans]


@router.get("/plans/{plan_id}", response_model=PlanRead)
async def get_plan(trip_id: UUID, plan_id: UUID, session: AsyncSession = Depends(get_session),
                   user: UserModel = Depends(get_current_user)):
    await _access(session, trip_id, user)
    result = await PlanService(session).get(trip_id, plan_id)
    if result is None:
        raise HTTPException(404, detail="Plan not found")
    return to_read(*result)


@router.patch("/plans/{plan_id}/nodes/{node_id}", response_model=PlanRead)
async def update_node(
    trip_id: UUID,
    plan_id: UUID,
    node_id: UUID,
    payload: PlanNodeUpdate,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    access = await _access(session, trip_id, user, "EDITOR", lock=True)
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_base_version", "reason"})
    if access.role != "OWNER" and payload.expected_base_version is None:
        raise HTTPException(422, detail="expected_base_version is required for shared trip edits")
    try:
        revised = await service.update_node(
            plan_id, node_id, changes, payload.reason,
            payload.expected_base_version, user.id, _actor_name(user),
        )
    except ValueError as exc:
        await session.rollback()
        if str(exc).startswith("PLAN_VERSION_CONFLICT:"):
            raise await _version_conflict(session, trip_id, service, exc) from exc
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    if revised is None:
        raise HTTPException(404, detail="Plan node not found")
    return to_read(*(await service.get(trip_id, revised.id)))


@router.delete("/plans/{plan_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    trip_id: UUID,
    plan_id: UUID,
    node_id: UUID,
    expected_base_version: int | None = None,
    reason: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: UserModel = Depends(get_current_user),
):
    access = await _access(session, trip_id, user, "EDITOR", lock=True)
    if access.role != "OWNER" and expected_base_version is None:
        raise HTTPException(422, detail="expected_base_version is required for shared trip edits")
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    try:
        revised = await service.delete_node(
            plan_id, node_id, reason, expected_base_version, user.id, _actor_name(user),
        )
    except ValueError as exc:
        await session.rollback()
        if str(exc).startswith("PLAN_VERSION_CONFLICT:"):
            raise await _version_conflict(session, trip_id, service, exc) from exc
        raise HTTPException(409, detail=str(exc)) from exc
    if revised is None:
        raise HTTPException(404, detail="Plan node not found")


@router.get("/plans/{plan_id}/changes", response_model=list[PlanChangeRead])
async def list_changes(trip_id: UUID, plan_id: UUID, session: AsyncSession = Depends(get_session),
                       user: UserModel = Depends(get_current_user)):
    await _access(session, trip_id, user)
    service = PlanService(session)
    if await service.get(trip_id, plan_id) is None:
        raise HTTPException(404, detail="Plan not found")
    return await service.changes(plan_id)
