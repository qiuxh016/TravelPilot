"""Verify USER_ADD persistence against local PostgreSQL without external providers."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select
from travelpilot_api.api.v1 import events as events_api
from travelpilot_api.schemas.events import ReplanRequest
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel
from travelpilot_infra.db.session import AsyncSessionFactory


class MatrixMaps:
    async def get_distance_matrix(self, points):
        if len(points) != 2:
            raise AssertionError("expected candidate and fixed booking coordinates")
        return [[0, 12], [12, 0]]


async def verify() -> None:
    suffix = uuid4().hex
    user_id, trip_id = uuid4(), uuid4()
    fixed_poi_id, add_poi_id = uuid4(), uuid4()
    event_id, plan_id = uuid4(), uuid4()
    local_timezone = timezone(timedelta(hours=8))
    trip_day = datetime.now(local_timezone).date() + timedelta(days=1)
    effective_at = datetime.combine(trip_day, time(11), tzinfo=local_timezone)
    original_provider = events_api.get_maps_provider
    events_api.get_maps_provider = MatrixMaps
    try:
        async with AsyncSessionFactory() as session:
            session.add(UserModel(
                id=user_id,
                email=f"user-add-{suffix}@example.invalid",
                username=f"user_add_{suffix[:12]}",
                password_hash="not-a-real-password",
                is_active=True,
                email_verified=True,
            ))
            await session.commit()
            session.add(TripModel(
                id=trip_id,
                owner_id=user_id,
                destination="成都",
                start_date=trip_day,
                end_date=trip_day,
                pace_level=3,
                status="READY",
            ))
            session.add_all([
                PoiModel(
                    id=fixed_poi_id,
                    name="数据库固定预约",
                    category="ATTRACTION",
                    latitude=Decimal("30.6000000"),
                    longitude=Decimal("104.0000000"),
                    suggested_duration_min=60,
                    opening_hours={"open": "09:00", "close": "20:00"},
                ),
                PoiModel(
                    id=add_poi_id,
                    name="数据库新增地点",
                    category="ATTRACTION",
                    latitude=Decimal("30.6100000"),
                    longitude=Decimal("104.0100000"),
                    suggested_duration_min=45,
                    opening_hours={"open": "09:00", "close": "20:00"},
                ),
            ])
            await session.commit()
            trip = await session.get(TripModel, trip_id)
            session.add_all([
                WishlistItemModel(trip_id=trip_id, poi_id=fixed_poi_id, priority=5),
                WishlistItemModel(trip_id=trip_id, poi_id=add_poi_id, priority=4),
                TripPlanModel(id=plan_id, trip_id=trip_id, version=1, status="READY"),
                TripEventModel(
                    id=event_id,
                    trip_id=trip_id,
                    event_type="USER_ADD",
                    effective_at=effective_at,
                    effective_timezone_offset_minutes=480,
                    payload={"poi_id": str(add_poi_id)},
                ),
            ])
            await session.commit()
            session.add(
                TripPlanNodeModel(
                    id=uuid4(),
                    plan_id=plan_id,
                    poi_id=fixed_poi_id,
                    day=trip_day,
                    start_time=time(14),
                    end_time=time(15),
                    priority=5,
                    status="FIXED",
                    note="confirmed booking",
                )
            )
            await session.commit()

            result = await events_api.create_replan(
                trip_id,
                ReplanRequest(event_id=event_id),
                session=session,
                _trip=trip,
            )
            saved_nodes = result["plan"].nodes
            fixed = next(node for node in saved_nodes if node.poi_id == fixed_poi_id)
            added = next(node for node in saved_nodes if node.poi_id == add_poi_id)
            workflow = await session.scalar(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.trip_id == trip_id,
                    WorkflowRunModel.kind == "REPLAN",
                )
            )
            assert fixed.status == "FIXED"
            assert fixed.start_time == time(14)
            assert added.start_time == time(11)
            assert added.end_time == time(11, 45)
            assert workflow.output_data["user_add"] == {
                "poi_id": str(add_poi_id),
                "outcome": "ADDED",
                "reason_code": "ADDED",
            }
            print("USER ADD DATABASE FLOW OK: fixed booking preserved, feasible node and diagnostic persisted")
    finally:
        events_api.get_maps_provider = original_provider
        async with AsyncSessionFactory() as cleanup:
            await cleanup.execute(delete(UserModel).where(UserModel.id == user_id))
            await cleanup.execute(delete(PoiModel).where(PoiModel.id.in_([fixed_poi_id, add_poi_id])))
            await cleanup.commit()


if __name__ == "__main__":
    asyncio.run(verify())
