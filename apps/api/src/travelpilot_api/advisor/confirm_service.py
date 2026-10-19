"""Application service for confirmation-gated Advisor plan writes."""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_core.application.trips import find_duplicate_trip, lock_trip_owner
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
from travelpilot_infra.db.repositories.plans import PlanRepository

from travelpilot_api.advisor.confirmation import (
    validate_confirmation_draft,
    validate_profile_memory_snapshot,
    validate_trip_snapshot,
)
from travelpilot_api.advisor.context import profile_memory_fingerprint
from travelpilot_api.advisor.interpretation import _active_personal_memories
from travelpilot_api.schemas.advisor import AdvisorConfirm, AdvisorConfirmRead


async def confirm_advisor_draft(
    db: AsyncSession,
    row: AdvisorSessionModel,
    payload: AdvisorConfirm,
    user: UserModel,
) -> AdvisorConfirmRead:
    if row.status == "CONFIRMED" and row.confirmed_trip_id and row.confirmed_plan_id:
        plan = await db.get(TripPlanModel, row.confirmed_plan_id)
        if plan is None:
            raise HTTPException(409, detail="已关联的行程计划不存在")
        return AdvisorConfirmRead(
            trip_id=row.confirmed_trip_id,
            plan_id=plan.id,
            plan_version=plan.version,
            already_confirmed=True,
        )
    context = row.context or {
        "revision": 0,
        "selected_candidate_ids": [],
        "current_plan": None,
        "confirmed_memories": [],
    }
    current_profile_memories = await _active_personal_memories(db, user.id)
    validate_profile_memory_snapshot(
        row.draft, profile_memory_fingerprint(current_profile_memories),
    )
    validate_confirmation_draft(
        row.draft,
        row.draft_version,
        payload.draft_version,
        int(context.get("revision", 0)),
        trip_start_date=row.start_date,
        trip_end_date=row.end_date,
        current_plan=context.get("current_plan"),
    )
    trip = None
    if row.trip_id:
        trip = await db.scalar(
            select(TripModel)
            .where(TripModel.id == row.trip_id, TripModel.owner_id == user.id)
            .with_for_update()
        )
        if trip is None:
            raise HTTPException(404, detail="旅行不存在")
        latest_plan = await db.scalar(
            select(TripPlanModel)
            .where(
                TripPlanModel.trip_id == trip.id,
            )
            .order_by(TripPlanModel.version.desc())
            .limit(1)
        )
        base_trip = context.get("base_trip")
        validate_trip_snapshot(
            snapshot_plan=context.get("current_plan"),
            latest_plan_id=str(latest_plan.id) if latest_plan else None,
            latest_plan_version=latest_plan.version if latest_plan else None,
            base_trip=base_trip,
            current_trip={
                "destination": trip.destination,
                "start_date": trip.start_date.isoformat(),
                "end_date": trip.end_date.isoformat(),
                "budget": str(trip.budget) if trip.budget is not None else None,
                "pace_level": trip.pace_level,
            },
        )
    else:
        if not row.start_date or not row.end_date:
            raise HTTPException(400, detail="行程日期缺失")
        await lock_trip_owner(db, user.id)
        duplicate = await find_duplicate_trip(
            db, user.id, row.destination, row.start_date, row.end_date
        )
        if duplicate is not None:
            raise HTTPException(
                409,
                detail="已有一段目的地和日期相同的旅行。请在顶部切换到已有旅行，再关联它继续规划。",
            )
        trip = TripModel(
            owner_id=user.id,
            destination=row.destination,
            start_date=row.start_date,
            end_date=row.end_date,
            budget=row.budget,
            pace_level=row.pace_level,
            status="DRAFT",
        )
        db.add(trip)
        await db.flush()
    selected_ids = row.draft.get("selected_candidate_ids", [])
    selected = {
        candidate["id"]: candidate
        for candidate in row.candidates
        if candidate["id"] in selected_ids
    }
    poi_ids: dict[str, UUID] = {}
    for candidate_id in selected_ids:
        candidate = selected.get(candidate_id)
        if candidate is None:
            raise HTTPException(409, detail="草案中的地点候选已失效")
        if candidate.get("existing_poi_id") and trip:
            poi = await db.scalar(
                select(PoiModel)
                .join(WishlistItemModel, WishlistItemModel.poi_id == PoiModel.id)
                .where(
                    PoiModel.id == UUID(candidate["existing_poi_id"]),
                    WishlistItemModel.trip_id == trip.id,
                )
            )
        else:
            poi = await db.scalar(
                select(PoiModel).where(
                    PoiModel.provider == candidate["provider"],
                    PoiModel.provider_id == candidate["provider_id"],
                )
            )
        if poi is None:
            poi = PoiModel(
                name=candidate["name"],
                category=candidate["category"],
                address=candidate["address"],
                latitude=Decimal(str(candidate["latitude"])),
                longitude=Decimal(str(candidate["longitude"])),
                suggested_duration_min=candidate["duration_min"],
                opening_hours=candidate.get("opening_hours"),
                provider=candidate["provider"],
                provider_id=candidate["provider_id"],
            )
            db.add(poi)
            await db.flush()
        poi_ids[candidate_id] = poi.id
        existing = await db.scalar(
            select(WishlistItemModel.id).where(
                WishlistItemModel.trip_id == trip.id,
                WishlistItemModel.poi_id == poi.id,
            )
        )
        if existing is None:
            db.add(
                WishlistItemModel(
                    trip_id=trip.id,
                    poi_id=poi.id,
                    priority=3,
                    interest_score=Decimal("0.500"),
                    notes=row.preference_text or None,
                    source_type="ADVISOR",
                )
            )
    version = await PlanRepository(db).next_version(trip.id)
    plan = TripPlanModel(
        trip_id=trip.id, version=version, status="READY", warnings=row.draft.get("warnings", [])
    )
    db.add(plan)
    await db.flush()
    for stop in row.draft.get("stops", []):
        candidate_id = stop["candidate_id"]
        if candidate_id not in poi_ids:
            raise HTTPException(409, detail="草案中的地点候选已失效")
        db.add(
            TripPlanNodeModel(
                plan_id=plan.id,
                poi_id=poi_ids[candidate_id],
                day=date.fromisoformat(stop["day"]),
                start_time=time.fromisoformat(stop["start_time"]),
                end_time=time.fromisoformat(stop["end_time"]),
                priority=3,
                status=stop.get("status", "PLANNED"),
                note=stop.get("note"),
            )
        )
    if row.trip_id:
        for change in row.draft.get("changes", []):
            if change["operation"] == "KEEP":
                continue
            db.add(
                PlanChangeModel(
                    plan_id=plan.id,
                    change_type={"ADD": "ADD", "REMOVE": "REMOVE", "MOVE": "UPDATE"}[
                        change["operation"]
                    ],
                    stable_key=change["stable_key"],
                    reason_code="ADVISOR_EDIT",
                    change_cost=change["change_cost"],
                    before_data=change["before"],
                    after_data=change["after"],
                    reason=change["reason"],
                )
            )
        trip.destination = row.destination
        trip.start_date = row.start_date
        trip.end_date = row.end_date
        trip.budget = row.budget
        trip.pace_level = row.pace_level
    row.status = "CONFIRMED"
    row.confirmed_trip_id = trip.id
    row.confirmed_plan_id = plan.id
    row.updated_at = datetime.now(UTC)
    db.add(
        AdvisorRunEventModel(
            owner_id=user.id,
            session_id=row.id,
            phase="user",
            operation="confirm_draft",
            status="SUCCEEDED",
            metadata_json={"plan_version": version, "created_trip": row.trip_id is None},
        )
    )
    db.add(
        AdvisorMessageModel(
            session_id=row.id,
            owner_id=user.id,
            role="assistant",
            content="草案已确认并保存到旅行。你可以在行程详情中继续调整。",
            payload={"trip_id": str(trip.id), "plan_id": str(plan.id)},
        )
    )
    await db.commit()
    return AdvisorConfirmRead(trip_id=trip.id, plan_id=plan.id, plan_version=version)
