"""Verify replan confirmation stays idempotent after response loss across processes."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from travelpilot_infra.db.models.plan import TripPlanModel, TripPlanNodeModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import TripEventModel, WorkflowRunModel
from travelpilot_infra.db.session import AsyncSessionFactory

ROOT = Path(__file__).resolve().parents[1]


async def _child(*, phase: str, owner_id: UUID, trip_id: UUID, run_id: UUID,
                 source_plan_id: UUID) -> None:
    from travelpilot_api.api.v1 import events
    from travelpilot_api.schemas.events import ReplanConfirm

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(UserModel).where(UserModel.id == owner_id))
        trip = await db.scalar(select(TripModel).where(
            TripModel.id == trip_id,
            TripModel.owner_id == owner_id,
        ))
        assert user is not None and trip is not None
        payload = ReplanConfirm(
            based_on_plan_id=source_plan_id,
            based_on_plan_version=1,
        )
        original_confirm = events.confirm_replan
        if phase == "INTERRUPT":
            async def confirm_then_exit(*args, **kwargs):
                await original_confirm(*args, **kwargs)
                # The actual handler has committed the new plan and confirmed
                # the WorkflowRun; simulate losing only its HTTP response.
                os._exit(79)

            events.confirm_replan = confirm_then_exit
        try:
            result = await events.confirm_replan(
                trip_id=trip_id,
                run_id=run_id,
                payload=payload,
                session=db,
                _trip=trip,
            )
            if phase == "INTERRUPT":
                raise AssertionError("confirmation interruption hook should exit after commit")
            assert result["plan"].version == 2
        finally:
            events.confirm_replan = original_confirm


def _run_child(*, phase: str, owner_id: UUID, trip_id: UUID, run_id: UUID,
               source_plan_id: UUID) -> None:
    env = dict(os.environ)
    env.update({
        "LLM_API_KEY": "",
        "LLM_BASE_URL": "",
        "LLM_MODEL": "",
        "AMAP_API_KEY": "",
        "MAPS_PROVIDER": "fake",
        "SMTP_HOST": "",
    })
    result = subprocess.run(
        [
            sys.executable, str(Path(__file__).resolve()), "--phase", phase,
            "--owner-id", str(owner_id), "--trip-id", str(trip_id),
            "--run-id", str(run_id), "--source-plan-id", str(source_plan_id),
        ],
        cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    expected = 79 if phase == "INTERRUPT" else 0
    if result.returncode != expected:
        raise RuntimeError(
            f"replan {phase} child exited {result.returncode}; "
            f"stdout={result.stdout[-2000:]}; stderr={result.stderr[-4000:]}"
        )


async def verify() -> None:
    owner_id, trip_id, poi_id = uuid4(), uuid4(), uuid4()
    source_plan_id, event_id, run_id = uuid4(), uuid4(), uuid4()
    token = uuid4().hex[:20]
    start_date = date.today() + timedelta(days=7)
    now = datetime.now(UTC)
    try:
        async with AsyncSessionFactory() as db:
            db.add(UserModel(
                id=owner_id,
                email=f"replan-{token}@example.invalid",
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
                start_date=start_date,
                end_date=start_date + timedelta(days=1),
                budget=None,
                pace_level=3,
                status="READY",
                created_at=now,
                updated_at=now,
            ))
            db.add(PoiModel(
                id=poi_id,
                name="重规划恢复验证地点",
                category="ATTRACTION",
                address="成都",
                latitude=Decimal("30.6600000"),
                longitude=Decimal("104.0600000"),
                suggested_duration_min=60,
                provider="fake",
                provider_id=f"replan-{token}",
            ))
            await db.commit()

        async with AsyncSessionFactory() as db:
            db.add(WishlistItemModel(
                trip_id=trip_id, poi_id=poi_id, priority=2,
                source_type="MANUAL", created_at=now,
            ))
            db.add(TripPlanModel(
                id=source_plan_id, trip_id=trip_id, version=1,
                status="READY", warnings=[], created_at=now,
            ))
            await db.flush()
            db.add(TripPlanNodeModel(
                plan_id=source_plan_id,
                poi_id=poi_id,
                day=start_date,
                start_time=time(9, 0),
                end_time=time(10, 0),
                priority=2,
                status="PLANNED",
                note=None,
            ))
            db.add(TripEventModel(
                id=event_id,
                trip_id=trip_id,
                event_type="WEATHER_CHANGE",
                effective_at=datetime.combine(start_date, time(8), tzinfo=UTC),
                effective_timezone_offset_minutes=480,
                payload={"condition": "RAIN"},
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
                        "name": "重规划恢复验证地点",
                        "day": start_date.isoformat(),
                        "start_time": "09:00",
                        "end_time": "10:00",
                        "priority": 2,
                        "status": "PLANNED",
                        "note": None,
                    }],
                    "warnings": [],
                }},
                created_at=now,
            ))
            await db.commit()

        _run_child(
            phase="INTERRUPT", owner_id=owner_id, trip_id=trip_id,
            run_id=run_id, source_plan_id=source_plan_id,
        )
        async with AsyncSessionFactory() as db:
            plans = (await db.scalars(select(TripPlanModel).where(
                TripPlanModel.trip_id == trip_id,
            ).order_by(TripPlanModel.version))).all()
            run = await db.scalar(select(WorkflowRunModel).where(
                WorkflowRunModel.id == run_id,
                WorkflowRunModel.trip_id == trip_id,
            ))
            assert [plan.version for plan in plans] == [1, 2]
            assert run is not None and run.status == "CONFIRMED"
            assert run.output_data["plan_id"] == str(plans[-1].id)

        _run_child(
            phase="RETRY", owner_id=owner_id, trip_id=trip_id,
            run_id=run_id, source_plan_id=source_plan_id,
        )
        async with AsyncSessionFactory() as db:
            plans = (await db.scalars(select(TripPlanModel).where(
                TripPlanModel.trip_id == trip_id,
            ).order_by(TripPlanModel.version))).all()
            nodes = (await db.scalars(select(TripPlanNodeModel).where(
                TripPlanNodeModel.plan_id == plans[-1].id,
            ))).all()
            assert len(plans) == 2 and sorted(plan.version for plan in plans) == [1, 2]
            assert len(nodes) == 1
        print(
            "PASS: event replan confirmation committed before response loss was retried "
            "in another process without creating a duplicate plan version."
        )
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.id == owner_id))
            await db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("INTERRUPT", "RETRY"))
    parser.add_argument("--owner-id")
    parser.add_argument("--trip-id")
    parser.add_argument("--run-id")
    parser.add_argument("--source-plan-id")
    args = parser.parse_args()
    if args.phase:
        if not all((args.owner_id, args.trip_id, args.run_id, args.source_plan_id)):
            parser.error("child phase requires owner, trip, run and source plan")
        asyncio.run(_child(
            phase=args.phase,
            owner_id=UUID(args.owner_id),
            trip_id=UUID(args.trip_id),
            run_id=UUID(args.run_id),
            source_plan_id=UUID(args.source_plan_id),
        ))
    else:
        asyncio.run(verify())
