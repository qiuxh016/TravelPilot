"""Verify confirmed lodging survives Advisor refresh and event replan in PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select
from travelpilot_api.advisor.context import refresh_trip_snapshot
from travelpilot_api.api.v1.events import confirm_replan
from travelpilot_api.schemas.events import ReplanConfirm
from travelpilot_infra.db.models.advisor import AdvisorSessionModel
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel
from travelpilot_infra.db.session import AsyncSessionFactory


async def verify() -> None:
    owner_id, trip_id, poi_id = uuid4(), uuid4(), uuid4()
    source_plan_id, event_id, run_id, advisor_session_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    token = uuid4().hex[:20]
    start_day = datetime.now(UTC).date() + timedelta(days=7)
    now = datetime.now(UTC)
    confirmed_lodging = [{
        "name": "PostgreSQL住宿持久化验证酒店",
        "check_in": start_day.isoformat(),
        "check_out": (start_day + timedelta(days=1)).isoformat(),
        "address": "本地集成验证专用数据",
        "source": "USER_CONFIRMED",
    }]
    selected_lodging = {
        "id": "selected-lodging-candidate",
        "name": "仍待加入的住宿候选",
        "planning_category": "LODGING",
        "latitude": 30.66,
        "longitude": 104.06,
    }
    advisor_context = {
        "revision": 1,
        "selected_candidate_ids": [selected_lodging["id"]],
        "candidate_schedules": {},
        "current_plan": {"id": None, "version": None, "stops": []},
        "base_trip": {},
        "planning_inputs": {},
    }
    try:
        async with AsyncSessionFactory() as db:
            db.add(UserModel(
                id=owner_id,
                email=f"lodging-pg-{token}@example.invalid",
                username=f"p{owner_id.hex[:23]}",
                password_hash="integration-test-only",
                display_name="",
                home_city="",
                bio="",
                is_active=True,
                email_verified=True,
                created_at=now,
            ))
            await db.commit()

        async with AsyncSessionFactory() as db:
            db.add(TripModel(
                id=trip_id,
                owner_id=owner_id,
                destination="成都",
                start_date=start_day,
                end_date=start_day + timedelta(days=1),
                budget=None,
                pace_level=3,
                status="READY",
                created_at=now,
                updated_at=now,
            ))
            db.add(PoiModel(
                id=poi_id,
                name="住宿持久化验证景点",
                category="ATTRACTION",
                address="成都",
                latitude=Decimal("30.6600000"),
                longitude=Decimal("104.0600000"),
                suggested_duration_min=60,
                provider="fake",
                provider_id=f"lodging-pg-{token}",
            ))
            await db.flush()
            db.add(TripPlanModel(
                id=source_plan_id,
                trip_id=trip_id,
                version=1,
                status="READY",
                warnings=[],
                lodging=confirmed_lodging,
                created_at=now,
            ))
            db.add(AdvisorSessionModel(
                id=advisor_session_id,
                owner_id=owner_id,
                trip_id=trip_id,
                mode="existing",
                destination="成都",
                start_date=start_day,
                end_date=start_day + timedelta(days=1),
                pace_level=3,
                preference_text="",
                candidates=[selected_lodging],
                context=advisor_context,
                draft=None,
                draft_version=0,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            ))
            await db.flush()
            db.add(TripPlanNodeModel(
                plan_id=source_plan_id,
                poi_id=poi_id,
                day=start_day,
                start_time=time(9),
                end_time=time(10),
                priority=2,
                status="PLANNED",
                note=None,
            ))
            db.add(TripEventModel(
                id=event_id,
                trip_id=trip_id,
                event_type="TIME_DELAY",
                effective_at=datetime.combine(start_day, time(8), tzinfo=UTC),
                effective_timezone_offset_minutes=480,
                payload={"delay_min": 30},
                created_at=now,
            ))
            db.add(WorkflowRunModel(
                id=run_id,
                trip_id=trip_id,
                kind="REPLAN",
                status="PREVIEWED",
                input_data={
                    "source_plan_id": str(source_plan_id),
                    "source_plan_version": 1,
                    "event_id": str(event_id),
                },
                output_data={"preview": {
                    "status": "READY",
                    "nodes": [{
                        "poi_id": str(poi_id),
                        "name": "住宿持久化验证景点",
                        "day": start_day.isoformat(),
                        "start_time": "09:30",
                        "end_time": "10:30",
                        "priority": 2,
                        "status": "PLANNED",
                        "note": None,
                    }],
                    "warnings": [],
                }},
                created_at=now,
            ))
            await db.commit()

        # Exercise the production context refresh against actual persisted rows,
        # then reload the JSONB session and plan through a fresh DB session.
        async with AsyncSessionFactory() as db:
            advisor_session = await db.get(AdvisorSessionModel, advisor_session_id)
            assert advisor_session is not None
            refreshed = await refresh_trip_snapshot(db, advisor_session)
            assert refreshed["current_plan"]["lodging"] == confirmed_lodging
            assert selected_lodging["id"] in refreshed["selected_candidate_ids"]
            await db.commit()
        async with AsyncSessionFactory() as db:
            advisor_session = await db.get(AdvisorSessionModel, advisor_session_id)
            source_plan = await db.get(TripPlanModel, source_plan_id)
            assert advisor_session is not None and source_plan is not None
            assert advisor_session.context["current_plan"]["lodging"] == confirmed_lodging
            assert selected_lodging["id"] in advisor_session.context["selected_candidate_ids"]
            assert source_plan.lodging == confirmed_lodging

        # Exercise the production event replan confirmation and verify the new
        # immutable plan version keeps lodging after a separate connection reads it.
        async with AsyncSessionFactory() as db:
            trip = await db.get(TripModel, trip_id)
            assert trip is not None
            result = await confirm_replan(
                trip_id=trip_id,
                run_id=run_id,
                payload=ReplanConfirm(
                    based_on_plan_id=source_plan_id,
                    based_on_plan_version=1,
                ),
                session=db,
                _trip=trip,
            )
            assert result["plan"].version == 2
            assert result["plan"].lodging == confirmed_lodging

        async with AsyncSessionFactory() as db:
            plans = (await db.scalars(
                select(TripPlanModel)
                .where(TripPlanModel.trip_id == trip_id)
                .order_by(TripPlanModel.version)
            )).all()
            assert [plan.version for plan in plans] == [1, 2]
            assert all(plan.lodging == confirmed_lodging for plan in plans)
            new_nodes = (await db.scalars(
                select(TripPlanNodeModel).where(
                    TripPlanNodeModel.plan_id == plans[-1].id,
                )
            )).all()
            assert len(new_nodes) == 1 and new_nodes[0].start_time == time(9, 30)

        # Finally refresh the same persisted Advisor session from plan v2.
        async with AsyncSessionFactory() as db:
            advisor_session = await db.get(AdvisorSessionModel, advisor_session_id)
            assert advisor_session is not None
            refreshed = await refresh_trip_snapshot(db, advisor_session)
            assert refreshed["current_plan"]["version"] == 2
            assert refreshed["current_plan"]["lodging"] == confirmed_lodging
            assert selected_lodging["id"] in refreshed["selected_candidate_ids"]
            await db.commit()
        async with AsyncSessionFactory() as db:
            advisor_session = await db.get(AdvisorSessionModel, advisor_session_id)
            assert advisor_session is not None
            assert advisor_session.context["current_plan"]["lodging"] == confirmed_lodging
            assert selected_lodging["id"] in advisor_session.context["selected_candidate_ids"]

        print(
            "PASS: real PostgreSQL persisted lodging through context refresh, "
            "event replan confirmation, fresh-session reload, and refresh from plan v2; "
            "selected session-only lodging remained selected."
        )
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.id == owner_id))
            await db.commit()


if __name__ == "__main__":
    asyncio.run(verify())
