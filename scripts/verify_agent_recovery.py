"""Verify interrupted AgentRun recovery against the configured local database."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select
from travelpilot_api.api.v1 import agent as agent_api
from travelpilot_api.api.v1.agent import PlannerRequest, run_planner_agent
from travelpilot_infra.db.models.plan import TripPlanModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import AgentRunModel
from travelpilot_infra.db.session import AsyncSessionFactory


async def verify() -> None:
    suffix = uuid4().hex
    user_id, trip_id, poi_id = uuid4(), uuid4(), uuid4()
    idempotency_key = f"recovery-{suffix}"
    interrupted = False
    original_plan_service = agent_api.PlanService
    original_api_key = agent_api.settings.llm_api_key
    original_model = agent_api.settings.llm_model

    class InterruptOncePlanService:
        def __init__(self, session):
            self.service = original_plan_service(session)

        async def generate(self, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise asyncio.CancelledError
            return await self.service.generate(*args, **kwargs)

        async def get(self, *args, **kwargs):
            return await self.service.get(*args, **kwargs)

    agent_api.settings.llm_api_key = ""
    agent_api.settings.llm_model = ""
    try:
        async with AsyncSessionFactory() as session:
            session.add(UserModel(
                id=user_id,
                email=f"agent-recovery-{suffix}@example.invalid",
                username=f"agent_rec_{suffix[:14]}",
                password_hash="not-a-real-password",
                is_active=True,
                email_verified=True,
            ))
            await session.commit()
            session.add(TripModel(
                id=trip_id,
                owner_id=user_id,
                destination="成都",
                start_date=date(2026, 10, 20),
                end_date=date(2026, 10, 21),
                pace_level=3,
                status="DRAFT",
            ))
            session.add(PoiModel(
                id=poi_id,
                name="恢复演练测试地点",
                category="MUSEUM",
                latitude=Decimal("30.6600000"),
                longitude=Decimal("104.0600000"),
                suggested_duration_min=60,
                opening_hours={"open": "09:00", "close": "18:00"},
            ))
            await session.commit()
            session.add(WishlistItemModel(
                trip_id=trip_id,
                poi_id=poi_id,
                priority=3,
                interest_score=Decimal("0.700"),
            ))
            await session.commit()

        agent_api.PlanService = InterruptOncePlanService
        async with AsyncSessionFactory() as session:
            trip = await session.get(TripModel, trip_id)
            try:
                await run_planner_agent(
                    trip_id,
                    PlannerRequest(preference="轻松一点"),
                    session=session,
                    maps=None,
                    _trip=trip,
                    idempotency_key=idempotency_key,
                )
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("the first execution must be interrupted")

        agent_api.PlanService = original_plan_service
        async with AsyncSessionFactory() as session:
            run = await session.scalar(select(AgentRunModel).where(
                AgentRunModel.trip_id == trip_id,
                AgentRunModel.idempotency_key == idempotency_key,
            ))
            assert run is not None and run.status == "RUNNING"
            assert run.input_data == {"preference": "轻松一点"}
            checkpoint = dict(run.output_data["checkpoint"])
            checkpoint["updated_at"] = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
            run.output_data = {**run.output_data, "checkpoint": checkpoint}
            await session.commit()

        async with AsyncSessionFactory() as session:
            trip = await session.get(TripModel, trip_id)
            recovered = await run_planner_agent(
                trip_id,
                PlannerRequest(preference="轻松一点"),
                session=session,
                maps=None,
                _trip=trip,
                idempotency_key=idempotency_key,
            )

        async with AsyncSessionFactory() as session:
            run = await session.scalar(select(AgentRunModel).where(
                AgentRunModel.trip_id == trip_id,
                AgentRunModel.idempotency_key == idempotency_key,
            ))
            plans = (await session.execute(select(TripPlanModel.id).where(
                TripPlanModel.trip_id == trip_id,
            ))).all()
            assert run is not None and run.status == "SUCCEEDED"
            assert "RECOVERING" in [item["state"] for item in run.output_data["checkpoints"]]
            assert recovered["status"] == "SUCCEEDED"
            assert len(plans) == 1
            replay = await run_planner_agent(
                trip_id,
                PlannerRequest(preference="轻松一点"),
                session=session,
                maps=None,
                _trip=await session.get(TripModel, trip_id),
                idempotency_key=idempotency_key,
            )
            assert replay["plan"].id == recovered["plan"].id
            print("AGENT RECOVERY OK: interrupted RUNNING record → stale checkpoint recovery → single committed plan → idempotent replay")
    finally:
        agent_api.PlanService = original_plan_service
        agent_api.settings.llm_api_key = original_api_key
        agent_api.settings.llm_model = original_model
        async with AsyncSessionFactory() as session:
            await session.execute(delete(UserModel).where(UserModel.id == user_id))
            await session.execute(delete(PoiModel).where(PoiModel.id == poi_id))
            await session.commit()


if __name__ == "__main__":
    asyncio.run(verify())
